"""命令行审查入口：固定 Git 快照、运行 Agent、保存事件和报告；不执行仓库代码。"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from git_input import GitInputError, load_git_input, scope_markdown
from review import (DeepSeekModel, Settings, MAX_MODEL_CALLS, REQUEST_TIMEOUT_SECONDS,
                    REVIEW_TIMEOUT_SECONDS, markdown_report, run_review)
from managed_review import review_repository


def main():
    parser = argparse.ArgumentParser(description="本地 Git 提交只读审查")
    parser.add_argument("--repo", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--base", help="修改前的提交")
    group.add_argument("--initial", action="store_true", help="显式审查初次导入：空快照到 head")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--output", required=True, help="新的结果目录，禁止覆盖")
    parser.add_argument("--notes", default="", help="项目补充说明，最多 2000 字符")
    parser.add_argument("--dataset-status", choices=("provided", "not_provided", "unknown"), default="unknown")
    parser.add_argument("--weights-status", choices=("provided", "not_provided", "unknown"), default="unknown")
    parser.add_argument("--exclude", action="append", default=[], help="仓库相对路径或通配符，可重复")
    parser.add_argument("--engine", choices=("managed", "legacy"), default="managed", help="默认分批审查；legacy 仅用于明确的旧版对照")
    parser.add_argument("--verification", choices=("on", "off"), default="on", help="默认复核候选；off保留为明确对照")
    args = parser.parse_args()
    target = Path(args.output).resolve()
    if target.exists():
        parser.error("输出目录已存在，请选择新的目录以保留所有试验。")
    try:
        context = {"notes": args.notes, "dataset": args.dataset_status, "weights": args.weights_status, "excluded_paths": args.exclude}
        example, metadata = load_git_input(args.repo, head=args.head, base=args.base, context=context)
    except GitInputError as error:
        parser.error(str(error))
    settings = Settings()
    if not settings.api_key:
        parser.error("没有可用的本机密钥，未启动真实模型。")
    example["project_context"]["notes"] = settings.redact(example["project_context"]["notes"])
    target.mkdir(parents=True)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "mode": "real",
              "model": settings.model, "transport": "beta-strict-function", "source": metadata, "engine": args.engine, "verification": args.verification if args.engine == "managed" else "not_applicable",
              "limits": {"model_calls": MAX_MODEL_CALLS, "request_seconds": REQUEST_TIMEOUT_SECONDS,
                         "task_seconds": REVIEW_TIMEOUT_SECONDS},
              "semantic_review": "pending", "events": [], "result": None}

    def save():
        temporary = target / "report.tmp"
        temporary.write_text(settings.redact(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")
        temporary.replace(target / "report.json")

    def emit(kind, data):
        report["events"].append({"type": kind, "data": data})
        save()
        if kind == "tool":
            print("工具：" + data["tool"] + " " + json.dumps(data["arguments"], ensure_ascii=False), flush=True)
        elif kind == "model":
            print("模型调用：" + str(data["step"]), flush=True)
        elif kind == "feedback":
            print(settings.redact(data["message"]), flush=True)

    save()
    print("提交：" + metadata["head_commit"], flush=True)
    print("已加载文本：%d 文件 / %d 字节；变更：%d 文件" % (
        len(example["after"]), metadata["after_scope"]["bytes"], len(metadata["changed_files"])), flush=True)
    report["result"] = (review_repository(example, lambda: DeepSeekModel(settings), emit, verify=args.verification == "on") if args.engine == "managed" else
                        run_review(example, DeepSeekModel(settings), emit))
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
    result = report["result"]
    scope = scope_markdown(metadata)
    (target / "REPORT.md").write_text(settings.redact(markdown_report(example, result, "real") + scope), encoding="utf-8")
    print("结果：%s，%d 条问题，%d 次调用，%.2f 秒" % (
        result["status"], len(result["findings"]), result["steps"], result["elapsed_seconds"]), flush=True)
    print("报告：" + str(target / "REPORT.md"), flush=True)
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
