import json
import unittest
from unittest.mock import patch

from examples import get_example
from git_input import snapshot
from replay import expected_finding
from resource_policy import ContextError, matching_rule, normalize_context, resource_kind
from review import CodeTools, markdown_report, run_review
from tests.test_review import SequenceModel, final


def sample(context=None):
    example = get_example("checkout")
    example["project_context"] = normalize_context(context)
    example["excluded_resources"] = {"before": [], "after": []}
    return example


def read(path):
    return {"action": "read_file", "arguments": {"path": path}}


class ResourcePolicyTests(unittest.TestCase):
    def test_context_validation_and_relative_patterns(self):
        context = normalize_context({"notes": "权重需自行准备", "weights": "not_provided", "excluded_paths": ["private\\", "private/"]})
        self.assertEqual(context["excluded_paths"], ["private/"])
        self.assertEqual(matching_rule("private/a.py", context["excluded_paths"]), "private/")
        self.assertEqual(matching_rule("cache/a.txt", ["cache/*.txt"]), "cache/*.txt")
        for bad in ({"dataset": "missing"}, {"notes": "x" * 2001}, {"extra": "private-value"},
                    {"excluded_paths": ["../private"]}, {"excluded_paths": ["C:/private"]},
                    {"excluded_paths": ["!private"]}, {"excluded_paths": ["x"] * 21}):
            with self.subTest(bad=list(bad)), self.assertRaises(ContextError) as error:
                normalize_context(bad)
            self.assertNotIn("private-value", str(error.exception))

    def test_resource_classification_preserves_source_code(self):
        self.assertEqual(resource_kind("external/image.png"), "dataset")
        self.assertEqual(resource_kind("volume.nii.gz"), "dataset")
        self.assertEqual(resource_kind("model.safetensors"), "weights")
        self.assertIsNone(resource_kind("data/loader.py"))

    def test_user_exclusion_happens_before_blob_read_and_budget(self):
        secret_id, source_id = "a" * 40, "b" * 40
        tree = ("100644 blob %s 9999999\tprivate.py\x00" % secret_id +
                "100644 blob %s 10\tmain.py\x00" % source_id).encode()
        batch = (source_id + " blob 10\n").encode() + b"value = 1\n\n"
        with patch("git_input.git", side_effect=[tree, batch]) as command:
            files, scope = snapshot("unused", "commit", context={"excluded_paths": ["private.py"]})
        self.assertEqual(files, {"main.py": "value = 1\n"})
        self.assertNotIn(secret_id, str(command.call_args_list[1:]))
        self.assertIn("用户排除", scope["excluded"][0]["reason"])

    def test_declared_missing_resources_are_skipped_and_cached(self):
        tools = CodeTools(sample({"dataset": "not_provided", "weights": "not_provided"}))
        for path in ("data/a.csv", "images/a.png", "weights/model.pth"):
            first = tools.execute("read_file", {"path": path})
            second = tools.execute("read_file", {"path": path})
            self.assertTrue(first["ok"])
            self.assertEqual(first["result"]["resource"]["status"], "not_provided")
            self.assertFalse(first["result"]["resource"]["lookup_performed"])
            self.assertTrue(second["result"]["cached"])
            self.assertEqual(second["result"]["resource"]["blocked_repeats"], 1)

    def test_provided_resource_is_a_declaration_not_execution_permission(self):
        result = CodeTools(sample({"dataset": "provided"})).execute("read_file", {"path": "images/a.png"})
        self.assertEqual(result["result"]["resource"]["status"], "not_checked")
        self.assertFalse(result["result"]["resource"]["lookup_performed"])

    def test_missing_path_checked_once_does_not_change_dataset_status(self):
        class TrackedDict(dict):
            lookups = 0
            def __contains__(self, key):
                self.lookups += 1
                return super().__contains__(key)
        example = sample()
        example["after"] = TrackedDict(example["after"])
        tools = CodeTools(example)
        for _ in range(2):
            result = tools.execute("read_file", {"path": "unknown.py"})
        self.assertEqual(example["after"].lookups, 1)
        self.assertEqual(result["result"]["resource"]["status"], "not_in_snapshot")
        self.assertEqual(tools.context["dataset"], "unknown")

    def test_exclusions_do_not_override_path_boundary(self):
        tools = CodeTools(sample({"excluded_paths": ["private/"]}))
        self.assertEqual(tools.execute("read_file", {"path": "private/a.py"})["result"]["resource"]["status"], "excluded_by_user")
        self.assertFalse(tools.execute("read_file", {"path": "../private/a.py"})["ok"])
        self.assertFalse(tools.execute("read_file", {"path": "data/a.csv", "version": "invalid"})["ok"])

    def test_missing_resources_do_not_abort_static_review_or_block_real_findings(self):
        example = sample({"notes": "数据和权重由使用者准备", "dataset": "not_provided", "weights": "not_provided"})
        model = SequenceModel([read("images/a.png"), read("images/b.png"), read("weights/model.pth"),
                               read("checkout.py"), final([expected_finding("checkout")])])
        events = []
        result = run_review(example, model, lambda kind, data: events.append((kind, data)))
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(len(result["resource_notes"]), 3)
        self.assertEqual(len([kind for kind, _ in events if kind == "resource"]), 3)
        self.assertEqual(result["verification_scope"]["dataset"], "not_verified")
        initial = json.loads(model.histories[0][1]["content"])
        self.assertEqual(initial["project_context"]["notes"], example["project_context"]["notes"])
        report = markdown_report(example, result, "real")
        self.assertIn("用户声明未提供", report)
        self.assertIn("不可用资源记录", report)

    def test_repeated_missing_resource_is_not_rechecked_or_reported_twice(self):
        example = sample({"dataset": "not_provided"})
        events = []
        result = run_review(example, SequenceModel([read("data/a.csv"), read("data/a.csv"), read("checkout.py"), final()]),
                            lambda kind, data: events.append((kind, data)))
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["resource_notes"]), 1)
        self.assertEqual(result["resource_notes"][0]["blocked_repeats"], 1)
        self.assertEqual(len([kind for kind, _ in events if kind == "resource"]), 1)

    def test_persistent_resource_requests_stop_with_records(self):
        result = run_review(sample(), SequenceModel([read("data/a.csv")] * 3), lambda *_: None)
        self.assertEqual(result["reason"], "repeated_call")
        self.assertEqual(result["resource_notes"][0]["requests"], 2)

    def test_interleaved_requests_cannot_evade_resource_retry_limit(self):
        result = run_review(sample(), SequenceModel([read("data/a.csv"), read("checkout.py"), read("data/a.csv"),
                                                     read("pricing.py"), read("data/a.csv")]), lambda *_: None)
        self.assertEqual(result["reason"], "resource_retry_limit")
        self.assertEqual(result["resource_notes"][0]["requests"], 3)
        self.assertEqual(result["resource_notes"][0]["blocked_repeats"], 2)

    def test_diff_only_keeps_project_scope_instructions(self):
        model = SequenceModel([final()])
        result = run_review(sample({"dataset": "not_provided"}), model, lambda *_: None, allow_tools=False)
        self.assertTrue(result["complete"])
        self.assertIn("不能单独当代码缺陷", model.histories[0][0]["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
