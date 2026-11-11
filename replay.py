"""网页离线演示脚本：固定动作与答案，不代表模型能力，也不作为真实失败的替代。"""
import json
import time


def reference(file, line, evidence):
    return {"file": file, "line": line, "evidence": evidence}


def expected_finding(example_id):
    """Kept out of the model-facing snapshots and initial prompts."""
    if example_id == "checkout":
        return {"file": "checkout.py", "line": 6, "severity": "high", "title": "将折扣比例当成金额直接扣减",
                "trigger": "subtotal=100、tier='gold' 时，应付 80 元，修改后却为 99.8 元。",
                "explanation": "discount_rate 返回 0～1 的比例。直接从小计减去该值，导致有折扣的会员被多收费。",
                "evidence": "    return round(subtotal - discount_rate(tier), 2)",
                "suggestion": "恢复 subtotal * (1 - discount_rate(tier))，并覆盖不同会员等级的测试。",
                "references": [reference("pricing.py", 6, "    return RATES.get(tier, 0.0)")]}
    if example_id == "accounts":
        return {"file": "accounts.py", "line": 6, "severity": "medium", "title": "查询不存在的用户时直接索引 None",
                "trigger": "email_for(999)，其中 999 不在 USERS 中。",
                "explanation": "新的 find_user 在未找到用户时返回 None。调用方仍索引 email，会触发 TypeError，丢失原先明确的 LookupError 行为。",
                "evidence": '    return user["email"].lower()',
                "suggestion": "检查 user is None 并抛出约定的 LookupError，或继续使用 require_user。",
                "references": [reference("repository.py", 12, "    return USERS.get(user_id)")]}
    return None


class ReplayModel:
    def __init__(self, example_id, delay=0.4):
        self.example_id, self.delay, self.index = example_id, delay, 0
        sequences = {
            "checkout": [("get_diff", {}), ("find_definition", {"symbol": "discount_rate"}),
                         ("read_file", {"path": "checkout.py"}), ("search_code", {"query": "discount_rate"})],
            "accounts": [("get_diff", {}), ("find_definition", {"symbol": "find_user"}),
                         ("read_file", {"path": "accounts.py"}), ("read_file", {"path": "repository.py"})],
            "refactor": [("get_diff", {}), ("read_file", {"path": "contracts.py"}),
                         ("read_file", {"path": "cart.py", "version": "before"}),
                         ("read_file", {"path": "cart.py"})],
        }
        self.actions = [{"action": name, "arguments": args} for name, args in sequences[example_id]]
        finding = expected_finding(example_id)
        self.actions.append({"action": "final", "summary": "已查看变更及相关接口约定。" +
                             ("发现一个需要修复的功能问题。" if finding else "在给定整数金额契约下，未发现可确认的行为变化。"),
                             "findings": [finding] if finding else []})

    def __call__(self, history, deadline):
        time.sleep(self.delay)
        action = self.actions[min(self.index, len(self.actions) - 1)]
        self.index += 1
        return json.dumps(action, ensure_ascii=False)
