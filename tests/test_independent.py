import json
import unittest

from tests.independent_cases import CASES, check_freeze, public_case
from tests.independent_oracles import verify_case
from tests.evaluate_independent import verifier_metrics


class IndependentTests(unittest.TestCase):
    def test_new_corpus_freeze_and_behavioral_oracles(self):
        check_freeze()
        self.assertEqual(len(CASES), 12)
        for case in CASES:
            with self.subTest(case=case["id"]):
                result = verify_case(case)
                self.assertTrue(result["before_passed"])
                self.assertTrue(result["valid"])

    def test_private_labels_and_oracles_are_not_in_public_input(self):
        for item in CASES:
            example, candidate = public_case(item)
            self.assertNotIn("expected", example)
            self.assertNotIn("split", example)
            self.assertNotIn("family", candidate)
            self.assertNotIn("verify_case", json.dumps([example, candidate]))
            example["description"] = "changed"
            self.assertNotEqual(example["description"], item["example"]["description"])

    def test_abstention_and_failure_are_not_hidden_in_accuracy(self):
        rows = [
            {"condition": "call-and-state", "expected": "supported", "verdict": "supported", "status": "judged"},
            {"condition": "call-and-state", "expected": "refuted", "verdict": "supported", "status": "judged"},
            {"condition": "call-and-state", "expected": "supported", "verdict": "uncertain", "status": "judged"},
            {"condition": "call-and-state", "expected": "supported", "verdict": "uncertain", "status": "invalid_response"},
        ]
        result = verifier_metrics(rows, 4)["call-and-state"]
        self.assertEqual(result["conditional_accuracy"], .5)
        self.assertEqual(result["decision_coverage"], .5)
        self.assertEqual(result["overall_match"], .25)
        self.assertEqual(result["false_supports"], 1)
        self.assertEqual(result["uncertain"], 1)
        self.assertEqual(result["invalid_or_failed"], 1)


if __name__ == "__main__":
    unittest.main()
