"""新增合成评测，标签与验收只留在评测端。模型入口只能使用 public_case()。"""
import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def finding(path, source, anchor, title, trigger):
    lines = source.splitlines()
    line = next(i + 1 for i, text in enumerate(lines) if anchor in text)
    return {"file": path, "line": line, "severity": "medium", "title": title, "trigger": trigger,
            "explanation": title + "，请检查相关调用是否真的满足该主张。", "evidence": lines[line - 1],
            "suggestion": "依据实际契约修复或否定该候选，不仅检查局部语句。", "references": []}


CASES = []


def pair(family, helper_name, helper, entry_name, before, bad, good, anchors, title, trigger, description, split):
    for source, expected, anchor in ((bad, "supported", anchors[0]), (good, "refuted", anchors[1])):
        identifier = "IE%03d" % (len(CASES) + 1)
        path = entry_name + ".py"
        example = {"id": identifier, "title": "接口实现调整", "description": description,
                   "before": {helper_name + ".py": helper, path: before},
                   "after": {helper_name + ".py": helper, path: source}, "project_context": {}}
        CASES.append({"id": identifier, "family": family, "split": split, "expected": expected,
                      "entry": entry_name, "example": example,
                      "candidate": finding(path, source, anchor, title, trigger)})


# 开发组：用于实现调用副作用和装饰器取证，不作为独立保留组成绩。
state_helper = ("def send(state, value):\n" + "    # transport setup\n" * 90 +
                "    state.sent = True\n    state.writes.append(value)\n    return True\n")
pair("state-side-effect", "state_support", state_helper, "publisher",
     "from state_support import send\n\ndef publish(state, value):\n    return send(state, value)\n",
     "from state_support import send\n\ndef publish(state, value):\n    state.writes.append(value)\n    return True\n",
     "from state_support import send\n\ndef publish(state, value):\n    delivered = send(state, value)\n    return delivered\n",
     ("return True", "return delivered"), "publish没有更新sent，成功发布后状态仍为False", "初始sent=False，发布非空消息",
     "publish成功后须记录消息并把state.sent置为True。", "dev")

decorator = "def handle_absent(fn):\n    def wrapped(value):\n        try:\n            return fn(value)\n        except LookupError:\n            return 'missing'\n    return wrapped\n"
pair("decorator-guard", "exception_support", decorator, "reader",
     "from exception_support import handle_absent\n\n@handle_absent\ndef load_value(value):\n    if value is None:\n        raise LookupError('absent')\n    return str(value)\n",
     "from exception_support import handle_absent\n\ndef load_value(value):\n    if value is None:\n        raise LookupError('absent')\n    return str(value)\n",
     "from exception_support import handle_absent\n\n@handle_absent\ndef load_value(value):\n    if value is None:\n        raise LookupError('absent')\n    text = str(value)\n    return text\n",
     ("raise LookupError", "raise LookupError"), "load_value未捕获LookupError，空输入会向调用方抛异常", "load_value(None)",
     "load_value(None)须返回'missing'，其他输入返回字符串。", "dev")

# 保留组：模型结果未观察前冻结；之后不得按成绩修改这些案例再称其为保留组。
pair("unit-conversion", "duration_support", "def to_milliseconds(seconds):\n    return seconds * 1000\n", "duration_entry",
     "from duration_support import to_milliseconds\n\ndef event_duration(seconds):\n    return to_milliseconds(seconds)\n",
     "from duration_support import to_milliseconds\n\ndef event_duration(seconds):\n    return seconds\n",
     "from duration_support import to_milliseconds\n\ndef event_duration(seconds):\n    duration = to_milliseconds(seconds)\n    return duration\n",
     ("return seconds", "return duration"), "event_duration未做毫秒转换，返回值会小1000倍", "输入2.5秒，应返回2500毫秒",
     "event_duration的输入单位是秒，输出单位必须是毫秒。", "holdout")

pair("expiry-boundary", "expiry_support", "def is_expired(now, expires):\n    return now >= expires\n", "cache_entry",
     "from expiry_support import is_expired\n\ndef cached_value(now, expires):\n    if is_expired(now, expires):\n        return None\n    return 'cached'\n",
     "from expiry_support import is_expired\n\ndef cached_value(now, expires):\n    if now > expires:\n        return None\n    return 'cached'\n",
     "from expiry_support import is_expired\n\ndef cached_value(now, expires):\n    expired = is_expired(now, expires)\n    if expired:\n        return None\n    return 'cached'\n",
     ("if now > expires", "expired ="), "缓存到期边界未包含等号，恰好到期仍返回缓存", "now=expires=100时应返回None",
     "到期时刻及之后不可返回缓存；到期之前返回'cached'。", "holdout")

pair("idempotency", "claim_support", "def claim(seen, key):\n    if key in seen:\n        return False\n    seen.add(key)\n    return True\n", "job_entry",
     "from claim_support import claim\n\ndef submit(seen, key):\n    if not claim(seen, key):\n        return False\n    return True\n",
     "from claim_support import claim\n\ndef submit(seen, key):\n    if key in seen:\n        return False\n    return True\n",
     "from claim_support import claim\n\ndef submit(seen, key):\n    accepted = claim(seen, key)\n    if not accepted:\n        return False\n    return True\n",
     ("return True", "accepted ="), "submit没有记录已处理key，同一个任务会被重复接受", "空seen对同一key连续调用两次",
     "submit第一次接受任务返回True，后续相同key返回False并保留已处理状态。", "holdout")

lease = "from contextlib import contextmanager\n\n@contextmanager\ndef lease(state):\n    state.open = True\n    try:\n        yield state\n    finally:\n        state.open = False\n"
pair("exception-cleanup", "lease_support", lease, "resource_entry",
     "from lease_support import lease\n\ndef run_job(state, fail):\n    with lease(state):\n        if fail:\n            raise ValueError('abort')\n        return 1\n",
     "from lease_support import lease\n\ndef run_job(state, fail):\n    state.open = True\n    if fail:\n        raise ValueError('abort')\n    state.open = False\n    return 1\n",
     "from lease_support import lease\n\ndef run_job(state, fail):\n    with lease(state) as active:\n        if fail:\n            raise ValueError('abort')\n        result = 1\n        return result\n",
     ("raise ValueError", "raise ValueError"), "异常分支没有清理open，失败后资源状态会残留True", "run_job(state, True)抛异常之后检查state.open",
     "run_job允许失败时抛ValueError，但成功或失败后state.open必须为False。", "holdout")


def public_case(item):
    """模型可接收的唯一入口；不包含split、expected、验收结果或冻结清单。"""
    return copy.deepcopy(item["example"]), copy.deepcopy(item["candidate"])


def manifest():
    serialized = json.dumps(CASES, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {"version": "independent-synthetic-v1", "cases_sha256": hashlib.sha256(serialized).hexdigest(),
            "public_builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "oracles_sha256": hashlib.sha256((ROOT / "independent_oracles.py").read_bytes()).hexdigest(),
            "dev": [item["id"] for item in CASES if item["split"] == "dev"],
            "holdout": [item["id"] for item in CASES if item["split"] == "holdout"]}


def check_freeze():
    expected = json.loads((ROOT / "independent-freeze.json").read_text(encoding="utf-8"))
    if manifest() != expected:
        raise ValueError("新增评测已冻结，案例或验收代码发生变化；不能继续沿用旧保留组身份")
    return expected
