"""冻结合成集的真实评测：给定候选复核对照，与端到端发现分别统计。"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from managed_review import review_repository
from review import DeepSeekModel, ModelError, Settings
from tests.independent_cases import CASES, check_freeze, public_case
from tests.independent_oracles import verify_case
from verification import verify_candidates


class SuiteModels:
    """跨案例的总保护；不把每个案例上限相加变成无界实验。"""
    def __init__(self, settings, max_calls, seconds, max_tokens):
        self.settings, self.max_calls = settings, max_calls
        self.deadline, self.max_tokens = time.monotonic() + seconds, max_tokens
        self.calls = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.usage_complete = True

    def available(self):
        return self.calls < self.max_calls and time.monotonic() < self.deadline - 2 and self.usage["prompt_tokens"] < self.max_tokens

    def factory(self):
        owner = self
        class Guarded:
            def __init__(self):
                self.inner = DeepSeekModel(owner.settings)
            def __getattr__(self, name):
                return getattr(self.inner, name)
            def __call__(self, messages, deadline):
                estimate = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 8000
                if not owner.available() or owner.usage["prompt_tokens"] + estimate > owner.max_tokens:
                    raise ModelError("评测总预算不足，未发送请求")
                owner.calls += 1
                before = dict(self.inner.usage)
                count = len(self.inner.usage_by_call)
                try:
                    return self.inner(messages, min(deadline, owner.deadline))
                finally:
                    for key, value in self.inner.usage.items():
                        owner.usage[key] = owner.usage.get(key, 0) + value - before.get(key, 0)
                    if len(self.inner.usage_by_call) == count:
                        owner.usage_complete = False
        return Guarded()


def verifier_metrics(rows, planned):
    output = {}
    for condition in ("anchor-only", "call-and-state"):
        selected = [row for row in rows if row["condition"] == condition]
        judged = [row for row in selected if row["status"] == "judged"]
        decided = [row for row in judged if row["verdict"] != "uncertain"]
        correct = sum(row["verdict"] == row["expected"] for row in decided)
        output[condition] = {"planned": planned, "judged": len(judged), "decided": len(decided), "correct": correct,
            "uncertain": sum(row["verdict"] == "uncertain" for row in judged),
            "invalid_or_failed": sum(row["status"] not in ("judged", "not_run") for row in selected),
            "not_run": sum(row["status"] == "not_run" for row in selected),
            "false_supports": sum(row["expected"] == "refuted" and row["verdict"] == "supported" for row in judged),
            "false_refutations": sum(row["expected"] == "supported" and row["verdict"] == "refuted" for row in judged),
            "decision_coverage": len(decided) / planned if planned else None,
            "conditional_accuracy": correct / len(decided) if decided else None,
            "overall_match": correct / planned if planned else None}
    return output


def summary_markdown(report):
    lines = ["# 新增合成候选评测", "", "分组：%s；模型：%s。" % (report["split"], report["model"]), "",
             "这是小型人工合成数据；给定候选的判别不是端到端缺陷识别准确率。未判断、失败和未执行都保留。", "",
             "| 条件 | 计划 | 有效结论 | 作出支持/反驳 | 正确判别 | 不确定 | 失败 | 未执行 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, row in report["verifier_metrics"].items():
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %d |" % (name, row["planned"], row["judged"], row["decided"], row["correct"], row["uncertain"], row["invalid_or_failed"], row["not_run"]))
    lines.extend(["", "## 给定候选的逐项结果", "", "| 案例 | 条件 | 预期 | 返回 | 状态 |", "| --- | --- | --- | --- | --- |"])
    for row in report["verifier_cases"]:
        lines.append("| %s | %s | %s | %s | %s |" % (row["case_id"], row["condition"], row["expected"], row["verdict"], row["status"]))
    lines.extend(["", "## 端到端发现", "", "| 案例 | 预置缺陷 | 原候选 | 复核保留 | 待核实 | 完成 |", "| --- | --- | --- | --- | --- | --- |"])
    for row in report["end_to_end_cases"]:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (row["case_id"], row["expected_bug"], row.get("raw_count", "—"), row.get("kept_count", "—"), row.get("uncertain_count", "—"), row.get("complete", False)))
    lines.extend(["", "端到端只列出告警与完成情况，报告语义仍待逐项复核，不能把位置命中直接记成正确缺陷。", "",
                  "真实请求：%d；已返回累计输入：%s；输出：%s；用量完整：%s。" % (report["actual_model_requests"], report["usage"]["prompt_tokens"], report["usage"]["completion_tokens"], report["usage_complete"]), "",
                  "冻结清单、运行代码摘要、条件顺序和原始证据包均保存在相邻report.json。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--mode", choices=("verifier", "end-to-end", "both"), default="both")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-calls", type=int, default=40)
    parser.add_argument("--time-limit", type=int, default=600)
    parser.add_argument("--max-prompt-tokens", type=int, default=300_000)
    args = parser.parse_args()
    if min(args.max_calls, args.time_limit, args.max_prompt_tokens) < 1:
        parser.error("评测预算必须是正数")
    root = Path(args.output)
    if root.exists():
        parser.error("评测目录已存在，禁止覆盖先前试验")
    freeze = check_freeze()
    cases = [item for item in CASES if item["split"] == args.split]
    oracles = {item["id"]: verify_case(item) for item in cases}
    if not all(row["valid"] for row in oracles.values()):
        parser.error("独立行为验收失败，未向模型发送请求")
    settings = Settings()
    if not settings.api_key:
        parser.error("未配置真实模型密钥")
    root.mkdir(parents=True)
    suite = SuiteModels(settings, args.max_calls, args.time_limit, args.max_prompt_tokens)
    project = Path(__file__).resolve().parent.parent
    runtime = {name: hashlib.sha256((project / name).read_bytes()).hexdigest() for name in
               ("verification.py", "managed_review.py", "retrieval.py", "review.py", "schemas.py")}
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "split": args.split, "mode": args.mode,
              "model": settings.model, "freeze": freeze, "runtime_sha256": runtime,
              "limits": {"calls": args.max_calls, "seconds": args.time_limit, "prompt_tokens": args.max_prompt_tokens},
              "oracles": oracles, "verifier_cases": [], "end_to_end_cases": [], "verifier_metrics": {}}

    def save():
        report.update(actual_model_requests=suite.calls, usage=suite.usage, usage_complete=suite.usage_complete)
        planned = len(cases) if args.mode in ("verifier", "both") else 0
        report["verifier_metrics"] = verifier_metrics(report["verifier_cases"], planned)
        temporary = root / "report.tmp"
        temporary.write_text(settings.redact(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")
        temporary.replace(root / "report.json")
    save()
    for item in cases:
        example, candidate = public_case(item)
        if args.mode in ("verifier", "both"):
            order = [False, True]
            if int(hashlib.sha256(item["id"].encode()).hexdigest()[0], 16) % 2:
                order.reverse()
            for enriched in order:
                name = "call-and-state" if enriched else "anchor-only"
                print("START %s %s" % (item["id"], name), flush=True)
                if not suite.available():
                    row = {"case_id": item["id"], "condition": name, "expected": item["expected"], "verdict": "uncertain", "status": "not_run"}
                else:
                    result = verify_candidates(example, [candidate], suite.factory, lambda *_: None, enriched=enriched,
                        deadline=min(suite.deadline, time.monotonic() + 75), max_calls=1, max_attempts=1, max_prompt_tokens=40_000)
                    row = dict(result["reviews"][0], case_id=item["id"], condition=name, expected=item["expected"], run=result)
                report["verifier_cases"].append(row)
                print("DONE %s %s %s/%s" % (item["id"], name, row["verdict"], row["status"]), flush=True)
                save()
        if args.mode in ("end-to-end", "both"):
            print("START %s end-to-end" % item["id"], flush=True)
            row = {"case_id": item["id"], "expected_bug": item["expected"] == "supported", "semantic_review": "pending"}
            if not suite.available():
                row.update(status="not_run", complete=False)
            else:
                events = []
                result = review_repository(example, suite.factory, lambda kind, data: events.append({"type": kind, "data": data}),
                    max_calls=min(6, args.max_calls - suite.calls), time_limit=min(90, suite.deadline - time.monotonic()), max_prompt_tokens=40_000)
                row.update(result=result, events=events, raw_count=len(result.get("candidates", [])), kept_count=len(result["findings"]),
                           uncertain_count=result.get("verification_summary", {}).get("uncertain", 0), complete=result["complete"], status=result["status"])
            report["end_to_end_cases"].append(row)
            print("DONE %s end-to-end %s" % (item["id"], row["status"]), flush=True)
            save()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
    (root / "REPORT.md").write_text(settings.redact(summary_markdown(report)), encoding="utf-8")
    print(json.dumps({"requests": suite.calls, "usage": suite.usage, "metrics": report["verifier_metrics"]}, ensure_ascii=True), flush=True)
    incomplete = any(row["status"] != "judged" for row in report["verifier_cases"]) or any(not row["complete"] for row in report["end_to_end_cases"])
    return 2 if incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
