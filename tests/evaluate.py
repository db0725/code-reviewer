"""Run all fixtures; keep evaluation expectations out of the review prompt."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from examples import EXAMPLES, get_example
from replay import ReplayModel
from review import DeepSeekModel, Settings, run_review
from managed_review import review_repository


EXPECTATIONS = {
    "checkout": "应指出折扣比例被当作金额扣减；100 元 gold 会员应付 80 元而非 99.8 元。",
    "accounts": "应指出 find_user 可能返回 None，调用方索引时导致 TypeError。",
    "refactor": "在给定非负整数分契约下没有预置缺陷，不应编造问题。",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("real", "replay"), default="replay")
    parser.add_argument("--engine", choices=("legacy", "managed"), default="legacy")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "results" / "evaluation-results.json"))
    args = parser.parse_args()
    if args.engine == "managed" and args.mode == "replay":
        parser.error("分批引擎不使用内置回放答案；请选择 --mode real，或运行固定响应单元检查。")
    target = Path(args.output)
    if target.exists():
        parser.error("评估记录已存在，请选择新的 --output 文件，保留历史实验。")
    settings = Settings()
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "mode": args.mode, "engine": args.engine,
              "note": "回放不衡量模型能力；真实结果的缺陷语义须人工按验收依据复核。", "cases": []}
    for key in EXAMPLES:
        print(key + ": starting", flush=True)
        case = {"example_id": key, "acceptance": EXPECTATIONS[key]}
        if args.mode == "real" and not settings.api_key:
            case.update(status="skipped", reason="未配置 OPENAI_API_KEY，未向真实模型发送请求")
        else:
            model = DeepSeekModel(settings) if args.mode == "real" else ReplayModel(key, delay=0)
            events = []
            def record_event(kind, data):
                events.append({"type": kind, "data": data})
                if args.mode == "real" and kind == "model":
                    print("%s: model step %s" % (key, data["step"]), flush=True)
            result = (review_repository(get_example(key), lambda: DeepSeekModel(settings), record_event) if args.engine == "managed" else
                      run_review(get_example(key), model, record_event))
            case.update(status=result["status"], result=result, events=events,
                        semantic_verdict="待人工复核" if args.mode == "real" else "不适用：固定脚本")
        report["cases"].append(case)
        print(key + ": " + case["status"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(settings.redact(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")
    print("评估记录已保存：" + str(target.resolve()))
    return 0 if all(case["status"] == "completed" for case in report["cases"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
