"""单独复核已保存的候选，不再次运行完整仓库发现流程。"""
import argparse
import json
import time
from pathlib import Path

from git_input import load_git_input
from review import DeepSeekModel, Settings, markdown_report
from verification import verify_candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--candidate-index", type=int, default=1)
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists():
        parser.error("输出目录已存在，不能覆盖旧复核")
    saved = json.loads(Path(args.report).read_text(encoding="utf-8"))
    source, result = saved["source"], saved["result"]
    candidates = result.get("candidates", result["findings"])
    if not 1 <= args.candidate_index <= len(candidates):
        parser.error("候选编号超出范围")
    example, metadata = load_git_input(args.repo, base=source["base_commit"], head=source["head_commit"], context=source.get("project_context"))
    settings = Settings()
    if not settings.api_key:
        parser.error("未配置真实模型密钥")
    target.mkdir(parents=True)
    events = []
    def emit(kind, data):
        events.append({"type": kind, "data": data})
        if kind == "model":
            print("复核调用：%d" % data["step"], flush=True)
    verification = verify_candidates(example, [candidates[args.candidate_index - 1]], lambda: DeepSeekModel(settings), emit,
                                     deadline=time.monotonic() + 75, max_calls=2, max_prompt_tokens=120_000)
    report = {"model": settings.model, "source": metadata, "original_report": str(Path(args.report).resolve()),
              "events": events, "verification": verification}
    (target / "report.json").write_text(settings.redact(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")
    counts = verification["counts"]
    output = {"status": "completed" if verification["complete"] else "limited", "complete": verification["complete"],
              "steps": verification["calls"], "elapsed_seconds": verification["elapsed_seconds"], "usage": verification["usage"],
              "candidate_reviews": verification["reviews"], "verification_summary": counts,
              "summary": "仅复核一个已保存候选，没有重新审查整个仓库；支持%d、反驳%d、待核实%d。" % (counts["supported"], counts["refuted"], counts["uncertain"]),
              "findings": [dict(row["candidate"], validation="模型静态复核支持，未执行验证") for row in verification["reviews"] if row["verdict"] == "supported"]}
    markdown = markdown_report(example, output, "real")
    markdown += "\n已返回用量：%s；用量完整：%s。\n" % (json.dumps(verification["usage"]), verification["usage_complete"])
    (target / "REPORT.md").write_text(settings.redact(markdown), encoding="utf-8")
    print(json.dumps({"counts": counts, "usage": verification["usage"], "complete": verification["complete"]}, ensure_ascii=True))
    return 0 if verification["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
