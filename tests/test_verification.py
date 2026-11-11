import json
import unittest

from managed_review import review_repository
from review import ModelError
from schemas import SchemaError
from tests.independent_cases import CASES, public_case
from tests.test_review import SequenceModel, final
from verification import candidate_id, evidence_packet, validate_verdict, verify_candidates


def verdict(packet, value, evidence=None):
    return {"candidate_id": packet["candidate_id"], "verdict": value, "rationale": "受控复核结论",
            "mechanism_assessment": "supported" if value == "supported" else "contradicted" if value == "refuted" else "unknown",
            "regression_assessment": "supported" if value == "supported" else "unknown",
            "impact_assessment": "supported" if value == "supported" else "unknown",
            "supporting_evidence": [evidence] if value == "supported" and evidence else [],
            "counterevidence": [evidence] if value == "refuted" and evidence else [],
            "limitations": ["仅静态证据"]}


def reference(example, version, path, query):
    lines = example[version][path].splitlines()
    line = next(i + 1 for i, text in enumerate(lines) if query in text)
    return {"version": version, "file": path, "line": line, "evidence": lines[line - 1]}


class VerdictModel:
    def __init__(self, decision):
        self.decision = decision
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def __call__(self, messages, deadline):
        self.usage["prompt_tokens"] += 100
        self.usage["completion_tokens"] += 20
        packet = json.loads(messages[1]["content"])
        return json.dumps(self.decision(packet), ensure_ascii=False)


class VerificationTests(unittest.TestCase):
    def test_callee_state_write_beyond_first_eighty_lines_is_retrieved(self):
        example, candidate = public_case(CASES[1])
        enriched = evidence_packet(example, candidate)
        baseline = evidence_packet(example, candidate, enriched=False)
        ref = reference(example, "after", "state_support.py", "state.sent = True")
        self.assertGreater(ref["line"], 80)
        self.assertFalse(any(s["file"] == ref["file"] for s in baseline["snippets"]))
        self.assertTrue(any(s["file"] == ref["file"] and ref["line"] in s["shown_lines"] for s in enriched["snippets"]))
        result = validate_verdict(example, enriched, json.dumps(verdict(enriched, "refuted", ref)))
        self.assertEqual(result["verdict"], "refuted")

    def test_decorator_guard_is_in_evidence_pack(self):
        example, candidate = public_case(CASES[3])
        pack = evidence_packet(example, candidate)
        self.assertTrue(any(s["file"] == "exception_support.py" and "except LookupError" in s["content"] for s in pack["snippets"]))

    def test_removed_call_retrieves_old_callee_not_only_after_calls(self):
        example, candidate = public_case(CASES[0])
        pack = evidence_packet(example, candidate)
        self.assertTrue(any(s["version"] == "before" and s["file"] == "state_support.py" and "state.sent = True" in s["content"] for s in pack["snippets"]))
        self.assertEqual(pack["task_context"], example["description"])

    def test_real_source_but_not_displayed_evidence_is_rejected(self):
        example, candidate = public_case(CASES[1])
        pack = evidence_packet(example, candidate, enriched=False)
        ref = reference(example, "after", "state_support.py", "state.sent = True")
        with self.assertRaisesRegex(ValueError, "未出现"):
            validate_verdict(example, pack, json.dumps(verdict(pack, "refuted", ref)))

    def test_wrong_candidate_empty_counterevidence_and_extra_text_rejected(self):
        example, candidate = public_case(CASES[1])
        pack = evidence_packet(example, candidate)
        ref = reference(example, "after", "state_support.py", "state.sent = True")
        valid = verdict(pack, "refuted", ref)
        with self.assertRaises(ValueError):
            validate_verdict(example, pack, json.dumps(dict(valid, candidate_id="wrong")))
        with self.assertRaises(ValueError):
            validate_verdict(example, pack, json.dumps(dict(valid, counterevidence=[])))
        with self.assertRaises(SchemaError):
            validate_verdict(example, pack, json.dumps(valid) + " explanation")

    def test_supported_cannot_ignore_unknown_or_contradicted_causal_check(self):
        example, candidate = public_case(CASES[0])
        pack = evidence_packet(example, candidate)
        ref = reference(example, "after", "publisher.py", "return True")
        bad = verdict(pack, "supported", ref)
        for value in ("unknown", "contradicted"):
            with self.assertRaisesRegex(ValueError, "全部获得支持"):
                validate_verdict(example, pack, json.dumps(dict(bad, impact_assessment=value)))

    def test_model_error_preserves_uncertain_candidate_and_unknown_usage(self):
        example, candidate = public_case(CASES[1])
        result = verify_candidates(example, [candidate], lambda: SequenceModel([ModelError("受控接口失败")]), lambda *_: None)
        self.assertEqual(result["reviews"][0]["status"], "model_error")
        self.assertEqual(result["counts"]["uncertain"], 1)
        self.assertFalse(result["complete"])
        self.assertFalse(result["usage_complete"])

    def test_call_budget_and_candidate_limit_do_not_silently_drop_candidates(self):
        example, candidate = public_case(CASES[1])
        result = verify_candidates(example, [candidate] * 4, lambda: SequenceModel([]), lambda *_: None, max_calls=0)
        self.assertEqual(len(result["reviews"]), 4)
        self.assertEqual(result["calls"], 0)
        self.assertEqual(result["counts"]["uncertain"], 4)

    def test_unsupported_final_does_not_consume_unbounded_retries(self):
        example, candidate = public_case(CASES[1])
        result = verify_candidates(example, [candidate], lambda: VerdictModel(lambda p: verdict(p, "refuted")),
                                   lambda *_: None, max_calls=1, max_attempts=2)
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["reviews"][0]["verdict"], "uncertain")

    def test_pipeline_keeps_rejected_candidate_and_shares_call_budget(self):
        example, candidate = public_case(CASES[1])
        ref = reference(example, "after", "state_support.py", "state.sent = True")
        models = [SequenceModel([final([candidate])]), VerdictModel(lambda p: verdict(p, "refuted", ref))]
        result = review_repository(example, lambda: models.pop(0), lambda *_: None, max_calls=4)
        self.assertTrue(result["complete"])
        self.assertEqual(result["steps"], 2)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertFalse(result["findings"])
        self.assertEqual(result["verification_summary"]["refuted"], 1)

    def test_pipeline_exhausted_budget_keeps_uncertainty(self):
        example, candidate = public_case(CASES[1])
        result = review_repository(example, lambda: SequenceModel([final([candidate])]), lambda *_: None, max_calls=1)
        self.assertEqual(result["steps"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["reason"], "verification_uncertain")
        self.assertEqual(len(result["candidate_reviews"]), 1)

    def test_candidate_id_does_not_depend_on_display_validation(self):
        _, candidate = public_case(CASES[1])
        self.assertEqual(candidate_id(candidate), candidate_id(dict(candidate, validation="展示字段")))


if __name__ == "__main__":
    unittest.main()
