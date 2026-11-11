"""本地网页服务：接收审查请求，后台运行任务，通过 SSE 把进度推送给网页。"""
import argparse
import json
import os
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from examples import EXAMPLES, get_example, public_example
from git_input import GitInputError, inspect_repository, load_git_input, scope_markdown
from replay import ReplayModel
from review import DeepSeekModel, Settings, markdown_report, run_review
from managed_review import ENGINE_VERSION, MAX_PROMPT_TOKENS, plan_summary, review_repository
from retrieval import review_plan

ROOT = Path(__file__).resolve().parent
TERMINAL = {"completed", "failed", "limited"}


class Job:
    """一次审查的内存记录；条件变量在新事件到来时唤醒正在等待的网页连接。"""

    def __init__(self, example_id, mode, example=None, source=None):
        self.id = uuid.uuid4().hex
        self.example_id, self.mode = example_id, mode
        self.example = example if example is not None else get_example(example_id)
        self.source = source or {"kind": "example"}
        self.status, self.result = "running", None
        self.events = []
        self.condition = threading.Condition()

    def emit(self, kind, data):
        with self.condition:
            event = {"id": len(self.events) + 1, "type": kind, "data": data}
            self.events.append(event)
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {"id": self.id, "example_id": self.example_id, "mode": self.mode,
                    "status": self.status, "result": self.result, "source": self.source}


class ReviewServer(ThreadingHTTPServer):
    """保存预览与任务；同一时间只允许一个任务调用模型。"""

    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, settings=None, model_factory=None, replay_delay=0.4):
        self.settings = settings or Settings()
        self.model_factory = model_factory or (lambda: DeepSeekModel(self.settings))
        self.replay_delay = replay_delay
        self.jobs, self.jobs_lock = {}, threading.Lock()
        self.previews = {}
        super().__init__(address, Handler)

    def new_job(self, example_id, mode, example=None, source=None):
        with self.jobs_lock:
            if any(job.status == "running" for job in self.jobs.values()):
                raise RuntimeError("已有审查任务正在运行，请等待结束")
            # 只在内存保留最近 30 个任务；重启后清空，不保存原始模型响应。
            while len(self.jobs) >= 30:
                del self.jobs[next(iter(self.jobs))]
            job = Job(example_id, mode, example, source)
            self.jobs[job.id] = job
        threading.Thread(target=self.run_job, args=(job,), daemon=True).start()
        return job

    def preview_git(self, repo, head, base, context=None):
        # 预览时固定代码与资源设置；开始审查时复用，避免仓库变化影响结果。
        example, metadata = load_git_input(repo, head=head, base=base, context=context)
        # Notes are user input, but known credentials still must not enter
        # preview responses, model context or saved reports.
        example["project_context"]["notes"] = self.settings.redact(example["project_context"]["notes"])
        preview_id = uuid.uuid4().hex
        source = dict(metadata, kind="git", preview_id=preview_id)
        source["review_plan"] = plan_summary(review_plan(example))
        source["review_engine"] = ENGINE_VERSION
        with self.jobs_lock:
            while len(self.previews) >= 10:
                del self.previews[next(iter(self.previews))]
            self.previews[preview_id] = {"example": example, "source": source, "created": time.monotonic()}
        return {"preview_id": preview_id, "example": public_example(example), "source": source}

    def new_git_job(self, preview_id):
        with self.jobs_lock:
            preview = self.previews.get(preview_id)
        if not preview or time.monotonic() - preview["created"] > 1800:
            raise GitInputError("变更预览已过期，请重新预览后审查。")
        return self.new_job(preview["example"]["id"], "real", preview["example"], preview["source"])

    def run_job(self, job):
        # HTTP 请求先返回任务编号，耗时的模型调用在后台线程中进行。
        example = job.example
        def emit(kind, data):
            # Redact before any model-generated content crosses into events or reports.
            data = json.loads(self.settings.redact(json.dumps(data, ensure_ascii=False)))
            job.emit(kind, data)
        emit("start", {"message": "离线脚本回放：仅展示控制流，不代表模型能力" if job.mode == "replay" else "开始真实模型审查", "mode": job.mode})
        try:
            if job.source["kind"] == "git":
                result = review_repository(example, self.model_factory, emit)
            else:
                model = ReplayModel(job.example_id, self.replay_delay) if job.mode == "replay" else self.model_factory()
                result = run_review(example, model, emit)
        except Exception:
            result = {"status": "failed", "reason": "server_error", "summary": "审查任务发生异常，未完成审查",
                      "findings": [], "steps": 0, "elapsed_seconds": 0, "usage": {}, "complete": False}
        result = json.loads(self.settings.redact(json.dumps(result, ensure_ascii=False)))
        result["markdown"] = markdown_report(example, result, job.mode)
        if job.source["kind"] == "git":
            result["markdown"] += scope_markdown(job.source)
        with job.condition:
            job.result, job.status = result, result["status"]
            job.emit("done", job.snapshot())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Avoid logging request bodies, model responses or query strings.
        pass

    def reply(self, status, content, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        elif isinstance(content, str):
            content = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(content)
        except ConnectionError:
            pass

    def allowed_host(self):
        return self.headers.get("Host", "") in {"127.0.0.1:%d" % self.server.server_port, "localhost:%d" % self.server.server_port}

    def do_GET(self):
        if not self.allowed_host():
            return self.reply(403, {"error": "仅允许本机访问"})
        path = urlsplit(self.path).path
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path in static:
            file, mime = static[path]
            return self.reply(200, (ROOT / "web" / file).read_bytes(), mime)
        if path == "/api/config":
            config = self.server.settings.public()
            default_repo = ROOT.parent / "DKNet"
            config["default_repository"] = str(default_repo) if default_repo.is_dir() else ""
            config["project_context_supported"] = True
            config["repository_review_engine"] = ENGINE_VERSION
            config["max_prompt_tokens"] = MAX_PROMPT_TOKENS
            return self.reply(200, config)
        if path == "/api/examples":
            return self.reply(200, [public_example(get_example(key)) for key in EXAMPLES])
        parts = path.strip("/").split("/")
        if len(parts) in (3, 4) and parts[:2] == ["api", "reviews"]:
            with self.server.jobs_lock:
                job = self.server.jobs.get(parts[2])
            if not job:
                return self.reply(404, {"error": "任务不存在或已过期，请重新发起审查"})
            if len(parts) == 3:
                return self.reply(200, job.snapshot())
            if parts[3] == "events":
                return self.stream_events(job)
            if parts[3] == "input":
                return self.reply(200, {"example": public_example(job.example), "source": job.source})
            if parts[3] == "report":
                if job.result is None:
                    return self.reply(409, {"error": "报告尚未生成"})
                return self.reply(200, job.result["markdown"], "text/markdown; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="review-%s.md"' % job.example_id})
        return self.reply(404, {"error": "页面或接口不存在"})

    def do_POST(self):
        if not self.allowed_host():
            return self.reply(403, {"error": "仅允许本机访问"})
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://127.0.0.1:%d" % self.server.server_port, "http://localhost:%d" % self.server.server_port}:
            return self.reply(403, {"error": "请求来源不匹配"})
        path = urlsplit(self.path).path
        if path not in ("/api/reviews", "/api/git/inspect", "/api/git/preview", "/api/git/reviews"):
            return self.reply(404, {"error": "接口不存在"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                raise ValueError("请求体大小不正确")
            self.connection.settimeout(5)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("需要 JSON 对象")
            if path.startswith("/api/git/"):
                return self.handle_git(path, body)
            if not isinstance(body, dict) or set(body) != {"example_id", "mode"}:
                raise ValueError("需要 example_id 和 mode")
            if not isinstance(body["example_id"], str) or body["example_id"] not in EXAMPLES:
                raise ValueError("未知示例")
            if body["mode"] not in ("real", "replay"):
                raise ValueError("mode 仅支持 real 或 replay")
            if body["mode"] == "real" and not self.server.settings.api_key:
                return self.reply(422, {"error": "未找到模型密钥。请检查本机 .env 配置或 OPENAI_API_KEY 环境变量并重启服务；也可以明确选择离线回放。"})
            job = self.server.new_job(body["example_id"], body["mode"])
        except (ValueError, TypeError, UnicodeDecodeError):
            self.close_connection = True
            return self.reply(400, {"error": "请求格式不正确，请选择有效示例和审查模式"})
        except (TimeoutError, OSError):
            self.close_connection = True
            return self.reply(408, {"error": "读取请求超时"})
        except RuntimeError as exc:
            return self.reply(409, {"error": str(exc)})
        self.reply(202, job.snapshot())

    def handle_git(self, path, body):
        try:
            if path == "/api/git/inspect":
                if set(body) != {"repository"} or not isinstance(body["repository"], str) or not body["repository"].strip():
                    raise GitInputError("请输入本地 Git 仓库路径。")
                return self.reply(200, inspect_repository(body["repository"]))
            if path == "/api/git/preview":
                if set(body) not in ({"repository", "head", "base"}, {"repository", "head", "base", "project_context"}) or not isinstance(body["repository"], str) or not body["repository"].strip():
                    raise GitInputError("需要仓库路径、目标提交和修改前提交（首次导入填空）。")
                if body["base"] is not None and not isinstance(body["base"], str):
                    raise GitInputError("修改前提交格式不正确。")
                return self.reply(200, self.server.preview_git(body["repository"], body["head"], body["base"], body.get("project_context")))
            if set(body) != {"preview_id"} or not isinstance(body["preview_id"], str):
                raise GitInputError("请先预览代码变更。")
            if not self.server.settings.api_key:
                return self.reply(422, {"error": "未找到模型密钥，请配置本机 .env 后重启服务。"})
            job = self.server.new_git_job(body["preview_id"])
            return self.reply(202, job.snapshot())
        except GitInputError as exc:
            return self.reply(400, {"error": str(exc)})
        except RuntimeError as exc:
            return self.reply(409, {"error": str(exc)})

    def stream_events(self, job):
        # SSE 向网页单向推送事件；断线后可按事件 ID 补发已有记录。
        try:
            cursor = max(0, int(self.headers.get("Last-Event-ID", "0")))
        except ValueError:
            return self.reply(400, {"error": "无效的事件位置"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                with job.condition:
                    events = [event for event in job.events if event["id"] > cursor]
                    terminal = job.status in TERMINAL
                    if not events and not terminal:
                        job.condition.wait(10)
                        events = [event for event in job.events if event["id"] > cursor]
                        terminal = job.status in TERMINAL
                for event in events:
                    packet = "id: %s\nevent: %s\ndata: %s\n\n" % (
                        event["id"], event["type"], json.dumps(event["data"], ensure_ascii=False))
                    self.wfile.write(packet.encode("utf-8"))
                    cursor = event["id"]
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                if terminal:
                    break
        except (ConnectionError, TimeoutError):
            pass


def main():
    parser = argparse.ArgumentParser(description="Code Review Agent 本地 Demo")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ReviewServer(("127.0.0.1", args.port))
    print("Code Review Agent: http://127.0.0.1:%d" % server.server_port, flush=True)
    print("真实模型：" + ("已配置密钥" if server.settings.api_key else "未配置密钥，可先使用离线回放"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
