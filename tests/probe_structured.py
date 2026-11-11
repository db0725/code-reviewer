"""Small real-provider compatibility checks; never execute returned actions."""
import json
import argparse
import time
from pathlib import Path

from review import DeepSeekModel, Settings, parse_action
from schemas import wire_action


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "results" / "structured-probe-final.json"))
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists():
        raise SystemExit("已有探针记录，请选择新的 --output 文件。")
    target.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    sample_finding = {"file": "sample.py", "line": 5, "severity": "medium", "title": "示例结构测试",
                      "trigger": "协议测试输入", "explanation": "仅检查字段与类型，不验证缺陷语义。",
                      "evidence": "return value", "suggestion": "供协议验证使用。", "references": []}
    cases = [(True, {"action": "get_diff", "arguments": {}}),
             (True, {"action": "read_file", "arguments": {"path": "sample.py", "version": "after"}}),
             (True, {"action": "search_code", "arguments": {"query": "value"}}),
             (True, {"action": "find_definition", "arguments": {"symbol": "sample"}}),
             (True, {"action": "final", "summary": "结构兼容性测试", "findings": [sample_finding]}),
             (False, {"action": "final", "summary": "仅最终报告结构测试", "findings": []})]
    rows = []
    for allow_tools, expected in cases:
        name = expected["action"] + ("-agent" if allow_tools else "-final-only")
        print("START " + name, flush=True)
        model = DeepSeekModel(settings)
        model.configure(allow_tools)
        try:
            raw = model([{"role": "system", "content": "This is a schema test. Submit exactly the requested step through review_step once. Do not choose a different action."},
                         {"role": "user", "content": json.dumps({"step": wire_action(expected)}, ensure_ascii=False)}], time.monotonic() + 60)
            parsed = parse_action(raw, envelope=True, allow_tools=allow_tools)
            row = {"case": name, "passed": parsed == expected, "structure_valid": True,
                   "requested_action": expected["action"], "returned_action": parsed["action"], "usage": model.usage}
        except Exception as error:
            row = {"case": name, "passed": False, "error_type": type(error).__name__, "message": settings.redact(str(error))}
        rows.append(row)
        print(name + ": " + ("PASS" if row["passed"] else "FAIL"), flush=True)
    report = {"protocol": "strict-function-json-schema", "strict": True, "structural_only": True, "tools_executed": False, "cases": rows}
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
