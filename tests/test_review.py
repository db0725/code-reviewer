import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from examples import EXAMPLES, diff_rows, get_example, public_example, unified_diff
from replay import ReplayModel, expected_finding
from review import (CodeTools, DeepSeekModel, FormatError, ModelError, ReviewTimeout,
                    Settings, SYSTEM, markdown_report, parse_action, read_local_settings, run_review, validate_findings)
from server import Handler, Job, ReviewServer


class SequenceModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.histories = []

    def __call__(self, history, deadline):
        self.histories.append(deepcopy(history))
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        return action if isinstance(action, str) else json.dumps(action, ensure_ascii=False)


def final(findings=None):
    return {"action": "final", "summary": "已检查给定代码", "findings": findings or []}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.example = get_example("checkout")
        self.tools = CodeTools(self.example)
        self.events = []

    def run_model(self, model, **kwargs):
        return run_review(self.example, model, lambda kind, data: self.events.append((kind, data)), **kwargs)

    def test_diff_line_mapping(self):
        rows = diff_rows("a\nb\nc\n", "a\nx\ny\nc\n")
        self.assertEqual([(r["kind"], r["old"], r["new"]) for r in rows],
                         [("context", 1, 1), ("delete", 2, None), ("add", None, 2),
                          ("add", None, 3), ("context", 3, 4)])
        self.assertIn("@@ -3,4 +3,4 @@", unified_diff(self.example))

    def test_search_and_ast_definition(self):
        search = self.tools.execute("search_code", {"query": "DISCOUNT_RATE"})
        self.assertTrue(search["ok"])
        self.assertEqual({m["file"] for m in search["result"]["matches"]}, {"pricing.py", "checkout.py"})
        found = self.tools.execute("find_definition", {"symbol": "discount_rate"})
        self.assertEqual(found["result"]["definitions"][0]["line"], 4)
        self.assertEqual(self.tools.execute("find_definition", {"symbol": "missing"})["result"]["definitions"], [])

    def test_versions_and_path_boundary(self):
        self.assertIn("subtotal *", self.tools.check_path("checkout.py", "before"))
        for path in ("../agent-harness/agent.py", "/etc/passwd", "C:/secret", "..\\secret", "checkout.py:secret", "./checkout.py", "missing.py", "checkout.py\x00"):
            with self.subTest(path=path):
                self.assertFalse(self.tools.execute("read_file", {"path": path})["ok"])
        self.assertFalse(self.tools.execute("read_file", {"path": "checkout.py", "version": "other"})["ok"])
        self.assertFalse(self.tools.execute("write_file", {"path": "x"})["ok"])

    def test_tool_argument_errors(self):
        for action, args in [("get_diff", {"x": 1}), ("read_file", {}), ("search_code", {"query": ""}),
                             ("find_definition", {"symbol": "bad.name"}), ("read_file", {"path": []}),
                             ("search_code", {"query": 5})]:
            self.assertFalse(self.tools.execute(action, args)["ok"])

    def test_evidence_and_deduplication(self):
        finding = expected_finding("checkout")
        self.assertEqual(len(validate_findings(self.example, [finding, finding])), 1)
        for change in ({"line": 500}, {"line": True}, {"evidence": "invented"}, {"file": "pricing.py"},
                       {"severity": "critical"}, {"references": [{"file": "pricing.py", "line": 1, "evidence": "invented"}]}):
            bad = dict(finding, **change)
            with self.subTest(change=change), self.assertRaises(FormatError):
                validate_findings(self.example, [bad])

    def test_all_replays_and_reports(self):
        for key in EXAMPLES:
            example = get_example(key)
            result = run_review(example, ReplayModel(key, delay=0), lambda *_: None)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["findings"]), 0 if key == "refactor" else 1)
            report = markdown_report(example, result, "replay")
            self.assertIn("离线脚本回放", report)
            self.assertEqual(result["steps"], 5)

    def test_format_error_feedback_and_recovery(self):
        model = SequenceModel(["```json\n{}\n```", {"action": "read_file", "arguments": {"path": "pricing.py"}}, final()])
        result = self.run_model(model)
        self.assertTrue(result["complete"])
        self.assertIn("格式或证据错误", model.histories[1][-1]["content"])
        self.assertTrue(any(kind == "feedback" for kind, _ in self.events))

    def test_evidence_error_can_be_corrected(self):
        bad = dict(expected_finding("checkout"), line=99)
        result = self.run_model(SequenceModel([final([bad]), final([expected_finding("checkout")])]))
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["steps"], 2)

    def test_format_errors_stop(self):
        for bad in ("not json", "[]", '{"action":"execute","arguments":{}}'):
            with self.subTest(bad=bad):
                result = self.run_model(SequenceModel([bad] * 3))
                self.assertEqual(result["reason"], "format_errors")
                self.assertFalse(result["complete"])

    def test_repeated_calls_stop_before_third_execution(self):
        call = {"action": "get_diff", "arguments": {}}
        result = self.run_model(SequenceModel([call] * 3))
        self.assertEqual(result["reason"], "repeated_call")
        self.assertEqual(len([e for e in self.events if e[0] == "tool"]), 2)

    def test_tool_error_feedback_and_recovery(self):
        model = SequenceModel([{"action": "read_file", "arguments": {"path": "unknown.py"}},
                               {"action": "read_file", "arguments": {"path": "pricing.py"}}, final()])
        result = self.run_model(model)
        self.assertTrue(result["complete"])
        self.assertIn('"ok": false', model.histories[1][-1]["content"])

    def test_limits_errors_and_empty_result_differ(self):
        limited = self.run_model(SequenceModel([{"action": "get_diff", "arguments": {}}]), max_steps=1)
        failed = self.run_model(SequenceModel([ModelError("连接失败")]))
        timeout = self.run_model(SequenceModel([ReviewTimeout("请求超时")]))
        empty = self.run_model(SequenceModel([final()]))
        self.assertEqual(limited["reason"], "max_steps")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(timeout["status"], "limited")
        self.assertTrue(empty["complete"])
        self.assertIn("审查未完成", markdown_report(self.example, limited, "real"))
        zero_time = self.run_model(SequenceModel([]), time_limit=0)
        self.assertEqual(zero_time["steps"], 0)

    def test_default_budget_can_finish_on_fiftieth_call(self):
        actions = [{"action": "read_file", "arguments": {"path": "checkout.py", "version": "before" if i % 2 else "after"}}
                   for i in range(49)]
        result = self.run_model(SequenceModel(actions + [final()]))
        self.assertTrue(result["complete"])
        self.assertEqual(result["steps"], 50)

    def test_default_budget_stops_before_fifty_first_call(self):
        actions = [{"action": "read_file", "arguments": {"path": "checkout.py", "version": "before" if i % 2 else "after"}}
                   for i in range(51)]
        model = SequenceModel(actions)
        result = self.run_model(model)
        self.assertEqual(result["reason"], "max_steps")
        self.assertEqual(len(model.histories), 50)

    def test_answers_are_not_in_model_input(self):
        model = SequenceModel([final()])
        self.run_model(model)
        initial = json.dumps(model.histories[0], ensure_ascii=False)
        self.assertNotIn(expected_finding("checkout")["title"], initial)
        self.assertNotIn("99.8", initial)
        public = json.dumps(public_example(self.example), ensure_ascii=False)
        self.assertNotIn("expected", public)

    def test_fixture_semantics_against_independent_oracles(self):
        # Only trusted built-in fixtures are executed by this test, never by the app or model.
        def load_snapshot(example_id, version, entry, dependency=None):
            files = get_example(example_id)[version]
            modules = {}
            if dependency:
                module = types.ModuleType(dependency)
                exec(compile(files[dependency + ".py"], dependency + ".py", "exec"), module.__dict__)
                modules[dependency] = module
            namespace = {}
            with patch.dict(sys.modules, modules):
                exec(compile(files[entry + ".py"], entry + ".py", "exec"), namespace)
            return namespace
        before = load_snapshot("checkout", "before", "checkout", "pricing")
        after = load_snapshot("checkout", "after", "checkout", "pricing")
        self.assertEqual(before["checkout"](100, "gold"), 80)
        self.assertEqual(after["checkout"](100, "gold"), 99.8)
        before = load_snapshot("accounts", "before", "accounts", "repository")
        after = load_snapshot("accounts", "after", "accounts", "repository")
        with self.assertRaises(LookupError):
            before["email_for"](999)
        with self.assertRaises(TypeError):
            after["email_for"](999)
        old = load_snapshot("refactor", "before", "cart")["total_cents"]
        new = load_snapshot("refactor", "after", "cart")["total_cents"]
        for items in ([], [{"price_cents": 100, "quantity": 0}],
                      [{"price_cents": 120, "quantity": 3}, {"price_cents": 15, "quantity": 2}]):
            expected = sum(item["price_cents"] * item["quantity"] for item in items)
            self.assertEqual(old(items), expected)
            self.assertEqual(new(items), expected)

    def test_late_final_is_not_accepted(self):
        def slow_model(history, deadline):
            time.sleep(0.015)
            return json.dumps(final())
        result = self.run_model(slow_model, time_limit=0.001)
        self.assertEqual(result["status"], "limited")
        self.assertFalse(result["complete"])

    def test_diff_only_baseline_cannot_execute_tools(self):
        model = SequenceModel([{"action": "read_file", "arguments": {"path": "pricing.py"}}, final()])
        result = self.run_model(model, allow_tools=False)
        self.assertTrue(result["complete"])
        self.assertFalse(any(kind == "tool" for kind, _ in self.events))
        self.assertTrue(any(kind == "feedback" for kind, _ in self.events))
        self.assertIn("格式或证据错误", model.histories[1][-1]["content"])

    def test_settings_redact_and_invalid_url(self):
        settings = Settings(api_key="not-for-browser-secret")
        self.assertNotIn("not-for-browser-secret", settings.redact("value=not-for-browser-secret"))
        self.assertNotIn("api_key", settings.public())
        for base in ("file:///secret", "https://user:password@example.com", "https://example.com?key=secret"):
            with self.assertRaises(ValueError):
                Settings(base_url=base)


class APIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server = ReviewServer(("127.0.0.1", 0), Settings(api_key=""), replay_delay=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)

    def test_pages_config_and_missing_key(self):
        with self.request("/") as response:
            self.assertIn("代码审查工作台", response.read().decode())
        with self.request("/api/config") as response:
            config = json.load(response)
            self.assertFalse(config["configured"])
            self.assertEqual(config["max_steps"], 50)
            self.assertEqual(config["timeout_seconds"], 300)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/reviews", {"example_id": "checkout", "mode": "real"})
        self.assertEqual(error.exception.code, 422)
        self.assertFalse(self.server.jobs)

    def test_second_server_cannot_bind_the_same_port(self):
        with self.assertRaises(OSError):
            ReviewServer(self.server.server_address, Settings(api_key=""))

    def test_replay_sse_resume_and_report(self):
        with self.request("/api/reviews", {"example_id": "checkout", "mode": "replay"}) as response:
            job = json.load(response)
        with self.request("/api/reviews/%s/events" % job["id"]) as response:
            events = response.read().decode()
        self.assertIn("event: done", events)
        self.assertIn("event: tool", events)
        with self.request("/api/reviews/%s/events" % job["id"], headers={"Last-Event-ID": "2"}) as response:
            resumed = response.read().decode()
        self.assertNotIn("id: 1\n", resumed)
        self.assertIn("event: done", resumed)
        with self.request("/api/reviews/%s/report" % job["id"]) as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertIn("99.8", response.read().decode())

    def test_invalid_requests(self):
        for body in ({"example_id": "../x", "mode": "replay"}, {"example_id": "checkout", "mode": "other"}, [], {"example_id": "checkout"}):
            with self.subTest(body=body), self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/reviews", body)
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/reviews", {"example_id": "checkout", "mode": "replay"}, headers={"Origin": "http://external.example"})
        self.assertEqual(error.exception.code, 403)

    def test_model_failure_never_falls_back(self):
        self.server.settings = Settings(api_key="test-secret")
        self.server.model_factory = lambda: SequenceModel([ModelError("模拟上游 HTTP 401")])
        with self.request("/api/reviews", {"example_id": "accounts", "mode": "real"}) as response:
            job = json.load(response)
        with self.request("/api/reviews/%s/events" % job["id"]) as response:
            events = response.read().decode()
        with self.request("/api/reviews/%s" % job["id"]) as response:
            done = json.load(response)
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["mode"], "real")
        self.assertFalse(done["result"]["complete"])
        self.assertNotIn("test-secret", events)

    def test_concurrent_job_rejected(self):
        self.server.replay_delay = 0.03
        with self.request("/api/reviews", {"example_id": "refactor", "mode": "replay"}) as response:
            first = json.load(response)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/reviews", {"example_id": "accounts", "mode": "replay"})
        self.assertEqual(error.exception.code, 409)
        with self.request("/api/reviews/%s/events" % first["id"]) as response:
            response.read()

    def test_sse_client_abort_is_normal_disconnect(self):
        class AbortedSocket:
            def write(self, value):
                raise ConnectionAbortedError("Windows client refreshed the page")
        handler = Handler.__new__(Handler)
        handler.headers = {}
        handler.wfile = AbortedSocket()
        handler.send_response = lambda *_: None
        handler.send_header = lambda *_: None
        handler.end_headers = lambda: None
        job = Job("checkout", "replay")
        job.status = "completed"
        job.emit("done", {})
        handler.stream_events(job)  # Must not emit a traceback when the browser disconnects.


class LocalSettingsTests(unittest.TestCase):
    def test_loads_local_config_without_changing_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('# comment\nOPENAI_API_KEY="test-local-only"\nOPENAI_MODEL=local-model\n'
                            "OPENAI_BASE_URL='https://api.deepseek.com/v1/'\nIGNORED=value\n", encoding="utf-8")
            settings = Settings(config_path=path)
            self.assertEqual(settings.api_key, "test-local-only")
            self.assertEqual(settings.model, "local-model")
            self.assertEqual(settings.base_url, "https://api.deepseek.com/v1")
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("test-local-only", json.dumps(settings.public()))

    def test_environment_and_explicit_arguments_override_local_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('OPENAI_API_KEY=local-key\nOPENAI_MODEL=local-model\n', encoding="utf-8")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "environment-key", "OPENAI_MODEL": "environment-model"}, clear=True):
                settings = Settings(config_path=path)
                self.assertEqual(settings.api_key, "environment-key")
                self.assertEqual(settings.model, "environment-model")
                explicit = Settings(api_key="explicit-key", model="explicit-model", config_path=path)
                self.assertEqual(explicit.api_key, "explicit-key")
                self.assertEqual(explicit.model, "explicit-model")
                self.assertEqual(Settings(api_key="", config_path=path).api_key, "")

    def test_missing_file_and_literal_values(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            self.assertFalse(Settings(config_path=path).api_key)
            path.write_text('OPENAI_API_KEY=$(do-not-execute)\n', encoding="utf-8")
            self.assertEqual(read_local_settings(path)["OPENAI_API_KEY"], "$(do-not-execute)")

    def test_invalid_config_errors_do_not_echo_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            for content in ('OPENAI_API_KEY="private-test-value\n', 'private-test-value\n'):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    read_local_settings(path)
                self.assertNotIn("private-test-value", str(caught.exception))


class UpstreamAdapterTests(unittest.TestCase):
    def test_real_http_protocol_and_usage(self):
        received = []
        mode = ["success"]
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                if mode[0] == "unauthorized":
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'upstream secret should never appear')
                    return
                payload = json.dumps({"choices": [{"finish_reason": "length" if mode[0] == "truncated" else "stop", "message": {"content": json.dumps(final())}}],
                                      "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            model = DeepSeekModel(Settings(api_key="test-only", base_url="http://127.0.0.1:%s" % upstream.server_port), protocol="legacy")
            content = model([{"role": "system", "content": "JSON"}], time.monotonic() + 5)
            self.assertEqual(parse_action(content)["action"], "final")
            self.assertEqual(model.usage["prompt_tokens"], 100)
            self.assertEqual(received[0]["response_format"], {"type": "json_object"})
            self.assertNotIn("test-only", json.dumps(received))
            mode[0] = "unauthorized"
            with self.assertRaises(ModelError) as error:
                model([], time.monotonic() + 5)
            self.assertIn("401", str(error.exception))
            self.assertNotIn("upstream secret", str(error.exception))
            mode[0] = "truncated"
            with self.assertRaises(ModelError) as error:
                model([], time.monotonic() + 5)
            self.assertIn("未完整结束", str(error.exception))
        finally:
            upstream.shutdown()
            upstream.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
