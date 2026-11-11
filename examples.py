"""网页内置的只读前后快照；不包含预置答案，真实模型与离线回放使用相同输入。"""
import difflib
from copy import deepcopy


EXAMPLES = {
    "checkout": {
        "id": "checkout", "title": "会员结算逻辑调整", "tag": "跨文件 · Python",
        "description": "结算服务调整了会员折扣的计算方式，检查接口约定与调用是否一致。",
        "before": {
            "checkout.py": 'from pricing import discount_rate\n\n\ndef checkout(subtotal, tier):\n    """Return the amount payable for a non-negative subtotal."""\n    return round(subtotal * (1 - discount_rate(tier)), 2)\n',
            "pricing.py": 'RATES = {"regular": 0.0, "silver": 0.1, "gold": 0.2}\n\n\ndef discount_rate(tier):\n    """Return the fraction of the subtotal discounted, in [0, 1]."""\n    return RATES.get(tier, 0.0)\n',
        },
        "after": {
            "checkout.py": 'from pricing import discount_rate\n\n\ndef checkout(subtotal, tier):\n    """Return the amount payable for a non-negative subtotal."""\n    return round(subtotal - discount_rate(tier), 2)\n',
            "pricing.py": 'RATES = {"regular": 0.0, "silver": 0.1, "gold": 0.2}\n\n\ndef discount_rate(tier):\n    """Return the fraction of the subtotal discounted, in [0, 1]."""\n    return RATES.get(tier, 0.0)\n',
        },
    },
    "accounts": {
        "id": "accounts", "title": "用户查询接口迁移", "tag": "接口契约 · Python",
        "description": "账户服务切换到新的查询函数，检查边界输入下的行为。",
        "before": {
            "accounts.py": 'from repository import require_user\n\n\ndef email_for(user_id):\n    user = require_user(user_id)\n    return user["email"].lower()\n',
            "repository.py": 'USERS = {1: {"email": "hello@example.com"}}\n\n\ndef require_user(user_id):\n    if user_id not in USERS:\n        raise LookupError("Unknown user")\n    return USERS[user_id]\n\n\ndef find_user(user_id):\n    """Return a user dict, or None when the user does not exist."""\n    return USERS.get(user_id)\n',
        },
        "after": {
            "accounts.py": 'from repository import find_user\n\n\ndef email_for(user_id):\n    user = find_user(user_id)\n    return user["email"].lower()\n',
            "repository.py": 'USERS = {1: {"email": "hello@example.com"}}\n\n\ndef require_user(user_id):\n    if user_id not in USERS:\n        raise LookupError("Unknown user")\n    return USERS[user_id]\n\n\ndef find_user(user_id):\n    """Return a user dict, or None when the user does not exist."""\n    return USERS.get(user_id)\n',
        },
    },
    "refactor": {
        "id": "refactor", "title": "购物车汇总函数重构", "tag": "行为保持 · Python",
        "description": "将循环改写为求和表达式，检查是否引入行为变化。输入为非负整数分。",
        "before": {
            "cart.py": 'def total_cents(items):\n    """Items contain non-negative integer price_cents and quantity."""\n    result = 0\n    for item in items:\n        result += item["price_cents"] * item["quantity"]\n    return result\n',
            "contracts.py": '# Cart API contract:\n# items is a list of dicts; price_cents and quantity are non-negative ints.\n# An empty list has total 0. No floating-point prices are accepted.\n',
        },
        "after": {
            "cart.py": 'def total_cents(items):\n    """Items contain non-negative integer price_cents and quantity."""\n    return sum(\n        item["price_cents"] * item["quantity"]\n        for item in items\n    )\n',
            "contracts.py": '# Cart API contract:\n# items is a list of dicts; price_cents and quantity are non-negative ints.\n# An empty list has total 0. No floating-point prices are accepted.\n',
        },
    },
}


def get_example(example_id):
    if example_id not in EXAMPLES:
        raise ValueError("未知示例")
    return deepcopy(EXAMPLES[example_id])


def changed_paths(example):
    return [p for p in example["after"] if example["before"].get(p) != example["after"][p]]


def unified_diff(example):
    chunks = []
    for path in changed_paths(example):
        chunks.extend(difflib.unified_diff(
            example["before"].get(path, "").splitlines(True),
            example["after"][path].splitlines(True),
            fromfile="a/" + path, tofile="b/" + path, n=3,
        ))
    return "".join(chunks)


def diff_rows(before, after):
    """把差异转换成网页行数据；旧行号和新行号分别计算，便于证据跳转。"""
    old, new = before.splitlines(), after.splitlines()
    rows = []
    for tag, i, j, k, l in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            rows.extend({"kind": "context", "old": a + 1, "new": b + 1, "text": old[a]}
                        for a, b in zip(range(i, j), range(k, l)))
        else:
            rows.extend({"kind": "delete", "old": a + 1, "new": None, "text": old[a]}
                        for a in range(i, j))
            rows.extend({"kind": "add", "old": None, "new": b + 1, "text": new[b]}
                        for b in range(k, l))
    return rows


def public_example(example):
    return {**{k: example[k] for k in ("id", "title", "tag", "description")},
            "files": [{"path": path, "changed": path in changed_paths(example),
                       "before": example["before"].get(path, ""), "after": text,
                       "rows": diff_rows(example["before"].get(path, ""), text)}
                      for path, text in example["after"].items()]}
