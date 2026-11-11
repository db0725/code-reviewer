"""仅执行本项目新建的可信微型夹具，不接收外部仓库代码或模型生成的代码。"""
import sys
import types
from unittest.mock import patch


def behavior(item, version):
    files = item["example"][version]
    modules = {path[:-3]: types.ModuleType(path[:-3]) for path in files}
    try:
        with patch.dict(sys.modules, modules):
            for path, source in files.items():
                exec(compile(source, "<trusted-independent-fixture:%s>" % path, "exec"), modules[path[:-3]].__dict__)
            entry = modules[item["entry"]]
            family = item["family"]
            if family == "state-side-effect":
                state = types.SimpleNamespace(sent=False, writes=[])
                return entry.publish(state, "x") is True and state.sent is True and state.writes == ["x"]
            if family == "decorator-guard":
                return entry.load_value(None) == "missing" and entry.load_value(3) == "3"
            if family == "unit-conversion":
                return entry.event_duration(2.5) == 2500 and entry.event_duration(0) == 0
            if family == "expiry-boundary":
                return entry.cached_value(100, 100) is None and entry.cached_value(99, 100) == "cached"
            if family == "idempotency":
                seen = set()
                return entry.submit(seen, "x") is True and entry.submit(seen, "x") is False and seen == {"x"}
            if family == "exception-cleanup":
                state = types.SimpleNamespace(open=False)
                try:
                    entry.run_job(state, True)
                except ValueError:
                    pass
                else:
                    return False
                return state.open is False and entry.run_job(state, False) == 1 and state.open is False
            raise ValueError("未知独立验收族")
    except (LookupError, ValueError):
        return False


def verify_case(item):
    before, after = behavior(item, "before"), behavior(item, "after")
    return {"before_passed": before, "after_passed": after,
            "expected_verdict": "refuted" if after else "supported",
            "valid": before and ("refuted" if after else "supported") == item["expected"]}
