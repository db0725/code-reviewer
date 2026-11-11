import json
import unittest

from examples import get_example
from managed_review import BoundedTools, detect_repository as review_repository
from review import ModelError
from replay import expected_finding
from retrieval import CodeIndex, code_window, review_plan
from tests.test_review import SequenceModel, final


def sample(count=1):
    return {"id": "local", "title": "受控仓库", "description": "只审代码", "project_context": {},
            "before": {"f%d.py" % i: "value = 1\n" for i in range(count)},
            "after": {"f%d.py" % i: "marker_%d = 2\n" % i for i in range(count)}}


def read(path, **kwargs):
    return {"action": "read_file", "arguments": dict(path=path, **kwargs)}


class MeteredModel(SequenceModel):
    def __init__(self, actions):
        super().__init__(actions)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.allowed = []

    def configure(self, allow_tools):
        self.allowed.append(allow_tools)

    def __call__(self, history, deadline):
        self.usage["prompt_tokens"] += 100
        self.usage["completion_tokens"] += 10
        return super().__call__(history, deadline)


class ManagedReviewTests(unittest.TestCase):
    def test_plan_keeps_all_changed_new_line_numbers_and_marks_docs(self):
        example = sample()
        example["before"]["f0.py"] = ""
        example["after"]["f0.py"] = "".join("value_%d = %d\n" % (i, i) for i in range(220))
        example["after"]["README.md"] = "资料只作上下文\n"
        plan = review_plan(example, unit_chars=4000)
        shown = [row["new"] for unit in plan["units"] for change in unit["changes"] for row in change["rows"] if row["new"]]
        self.assertEqual(sorted(shown), list(range(1, 221)))
        self.assertGreater(len(plan["units"]), 1)
        self.assertEqual(plan["context_only_files"], ["README.md"])
        self.assertFalse(plan["uncovered"])

    def test_long_lines_remain_explicitly_uncovered(self):
        example = sample()
        example["after"]["f0.py"] = "value = '" + "x" * 30000 + "'\n"
        result = review_repository(example, lambda: SequenceModel([final()]), lambda *_: None)
        self.assertFalse(result["complete"])
        self.assertEqual(result["coverage"]["pending_files"], ["f0.py"])
        self.assertTrue(result["coverage"]["uncovered"])

    def test_partial_line_read_is_bounded_and_invalid_range_rejected(self):
        example = sample()
        example["after"]["f0.py"] = "".join("value_%d = 1\n" % i for i in range(500))
        tools = BoundedTools(example)
        tools.begin(review_plan(example)["units"][0])
        result = tools.execute("read_file", {"path": "f0.py", "start_line": 120, "end_line": 500})["result"]
        self.assertEqual(result["shown_lines"], list(range(120, 200)))
        self.assertEqual(result["next_line"], 200)
        self.assertTrue(result["truncated"])
        self.assertFalse(tools.execute("read_file", {"path": "f0.py", "start_line": 0})["ok"])

    def test_duplicate_diff_and_read_do_not_repeat_large_content(self):
        example = sample()
        tools = BoundedTools(example)
        tools.begin(review_plan(example)["units"][0])
        self.assertNotIn("diff", tools.execute("get_diff", {})["result"])
        first = tools.execute("read_file", {"path": "f0.py"})
        second = tools.execute("read_file", {"path": "f0.py"})
        self.assertIn("content", first["result"])
        self.assertTrue(second["result"]["cached"])
        self.assertNotIn("content", second["result"])

    def test_unseen_reference_is_rejected_until_read(self):
        example = get_example("checkout")
        model = MeteredModel([final([expected_finding("checkout")]), read("pricing.py"), final([expected_finding("checkout")])])
        events = []
        result = review_repository(example, lambda: model, lambda kind, data: events.append((kind, data)))
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["usage"]["prompt_tokens"], 300)
        self.assertEqual([row["usage"]["prompt_tokens"] for row in result["telemetry"]], [100, 100, 100])
        self.assertEqual(len([kind for kind, _ in events if kind == "feedback"]), 1)

    def test_force_final_does_not_fake_completion_for_uncooperative_model(self):
        model = MeteredModel([read("f0.py")] * 4)
        result = review_repository(sample(), lambda: model, lambda *_: None)
        self.assertFalse(result["complete"])
        self.assertEqual(model.allowed, [True, True, True, False])
        self.assertEqual(result["coverage"]["pending_files"], ["f0.py"])
        self.assertEqual(result["steps"], 4)

    def test_batches_have_independent_histories_and_usage_is_summed(self):
        models = []
        def factory():
            model = MeteredModel([final()])
            models.append(model)
            return model
        result = review_repository(sample(6), factory, lambda *_: None)
        self.assertTrue(result["complete"])
        self.assertEqual(len(models), 2)
        self.assertNotIn("marker_0", json.dumps(models[1].histories[0]))
        self.assertEqual(result["usage"], {"prompt_tokens": 200, "completion_tokens": 20})

    def test_global_budget_preserves_pending_scope(self):
        result = review_repository(sample(6), lambda: MeteredModel([final()]), lambda *_: None, max_calls=1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["reason"], "max_steps")
        self.assertEqual(result["coverage"]["completed_batches"], 1)
        self.assertEqual(result["coverage"]["pending_files"], ["f5.py"])

    def test_token_preflight_stops_before_model_is_called(self):
        model = MeteredModel([final()])
        result = review_repository(sample(), lambda: model, lambda *_: None, max_prompt_tokens=1)
        self.assertEqual(result["reason"], "token_budget")
        self.assertEqual(result["steps"], 0)
        self.assertFalse(model.histories)

    def test_failed_request_does_not_reuse_previous_call_latency(self):
        class Failing(MeteredModel):
            def __init__(self):
                super().__init__([{"action": "get_diff", "arguments": {}}])
                self.usage_by_call = []
            def __call__(self, history, deadline):
                if self.usage_by_call:
                    raise ModelError("受控网络失败")
                action = super().__call__(history, deadline)
                self.usage_by_call.append({"seconds": 1.0, "request_bytes": 100, "usage": dict(self.usage)})
                return action
        result = review_repository(sample(), Failing, lambda *_: None)
        self.assertEqual(result["reason"], "model_error")
        self.assertFalse(result["usage_complete"])
        self.assertNotIn("seconds", result["telemetry"][-1])

    def test_deadline_stops_before_call_and_unknown_usage_is_not_success_cost(self):
        model = MeteredModel([final()])
        result = review_repository(sample(), lambda: model, lambda *_: None, time_limit=0)
        self.assertFalse(result["complete"])
        self.assertEqual(result["reason"], "timeout")
        self.assertEqual(result["steps"], 0)
        unknown = review_repository(sample(), lambda: SequenceModel([final()]), lambda *_: None)
        self.assertFalse(unknown["usage_complete"])

    def test_search_and_definition_index_survive_unrelated_invalid_python(self):
        index = CodeIndex({"bad.py": "def broken(\n", "good.py": "def unique_function():\n    return 1\n"})
        self.assertEqual(index.find("unique_function")[0]["file"], "good.py")
        self.assertEqual(index.search("unique_function")[0]["file"], "good.py")

    def test_overlong_source_line_is_never_returned_as_half_evidence(self):
        result = code_window("x" * 10000 + "\nreturn_value = 1\n", max_chars=100)
        self.assertEqual(result["omitted_lines"], [1])
        self.assertEqual(result["shown_lines"], [2])


if __name__ == "__main__":
    unittest.main()
