import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from examples import EXAMPLES
from review import DeepSeekModel, ModelError, SchemaFormatError, Settings, parse_action, run_review


class StrictToolTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.mode = "success"
        owner = self

        class Stub(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.requests.append({"path": self.path, "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                call = {"type": "function", "id": "test-call", "function": {
                    "name": "review_step", "arguments": json.dumps({"step": {"action": "final", "summary": "检查完成", "findings": []}})}}
                calls = [call]
                mode = owner.mode
                if mode == "verification":
                    call["function"] = {"name": "verify_candidate", "arguments": json.dumps({
                        "candidate_id": "fixture", "verdict": "uncertain", "rationale": "证据不足",
                        "mechanism_assessment": "unknown", "regression_assessment": "unknown", "impact_assessment": "unknown",
                        "supporting_evidence": [], "counterevidence": [], "limitations": ["缺少调用契约"]})}
                if mode in ("tool_first", "tool_first_nullable") and len(owner.requests) == 1:
                    call["function"]["arguments"] = json.dumps({"step": {"action": "read_file", "arguments": {"path": "pricing.py", "version": "after"}}})
                    if mode == "tool_first_nullable":
                        action = json.loads(call["function"]["arguments"])
                        action["step"]["arguments"].update(start_line=None, end_line=None)
                        call["function"]["arguments"] = json.dumps(action)
                if mode == "recover":
                    mode = "multiple" if len(owner.requests) == 1 else "success"
                if mode == "multiple":
                    calls.append(dict(call))
                elif mode == "wrong_name":
                    call["function"]["name"] = "write_file"
                elif mode == "malformed":
                    call["function"]["arguments"] = "not json"
                elif mode == "empty":
                    calls = []
                payload = {"choices": [{"finish_reason": "length" if mode == "truncated" else "tool_calls", "message": {
                    "content": "extra prose" if mode == "prose" else "", "tool_calls": calls,
                    "refusal": "refused" if mode == "refusal" else None}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 25}}
                encoded = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def model(self, suffix=""):
        return DeepSeekModel(Settings(api_key="test-placeholder", base_url="http://127.0.0.1:%s%s" % (self.server.server_port, suffix)))

    def test_default_transport_requires_strict_schema_and_counts_usage(self):
        model = self.model()
        self.assertEqual(parse_action(model([], time.monotonic() + 5), envelope=True)["action"], "final")
        request = self.requests[0]
        self.assertEqual(request["path"], "/beta/chat/completions")
        function = request["body"]["tools"][0]["function"]
        self.assertIs(function["strict"], True)
        self.assertEqual(request["body"]["tool_choice"]["function"]["name"], function["name"])
        self.assertEqual(model.usage, {"prompt_tokens": 100, "completion_tokens": 25})
        self.assertNotIn("test-placeholder", json.dumps(request))

    def test_beta_suffix_and_final_only_schema(self):
        model = self.model("/beta")
        model.configure(False)
        model([], time.monotonic() + 5)
        self.assertEqual(self.requests[0]["path"], "/beta/chat/completions")
        schema = self.requests[0]["body"]["tools"][0]["function"]["parameters"]
        self.assertEqual(schema["properties"]["step"]["properties"]["action"]["enum"], ["final"])

    def test_multiple_calls_prose_unknown_function_and_empty_are_rejected(self):
        for mode in ("multiple", "prose", "wrong_name", "empty"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(SchemaFormatError):
                self.model()([], time.monotonic() + 5)

    def test_incomplete_or_refused_is_not_executable(self):
        for mode in ("truncated", "refusal"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(ModelError):
                self.model()([], time.monotonic() + 5)

    def test_transport_feedback_recovers_without_executing_rejected_calls(self):
        self.mode = "recover"
        events = []
        result = run_review(EXAMPLES["checkout"], self.model(), lambda kind, data: events.append((kind, data)))
        self.assertTrue(result["complete"])
        self.assertEqual(result["steps"], 2)
        self.assertEqual([kind for kind, _ in events].count("feedback"), 1)
        self.assertNotIn("tool", [kind for kind, _ in events])
        self.assertNotIn("tool_calls", json.dumps(self.requests[1]["body"]["messages"]))

    def test_malformed_arguments_stop_after_three_attempts(self):
        self.mode = "malformed"
        result = run_review(EXAMPLES["checkout"], self.model(), lambda *_: None)
        self.assertEqual(result["reason"], "format_errors")
        self.assertEqual(result["steps"], 3)

    def test_native_history_pairs_generated_call_with_actual_tool_result(self):
        self.mode = "tool_first"
        result = run_review(EXAMPLES["checkout"], self.model(), lambda *_: None)
        self.assertTrue(result["complete"])
        self.assertEqual(result["steps"], 2)
        messages = self.requests[1]["body"]["messages"]
        self.assertEqual(messages[-2]["role"], "assistant")
        self.assertEqual(messages[-2]["tool_calls"][0]["id"], messages[-1]["tool_call_id"])
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertIn("RATES", messages[-1]["content"])

    def test_nullable_line_fields_keep_native_tool_call_pairing(self):
        self.mode = "tool_first_nullable"
        result = run_review(EXAMPLES["checkout"], self.model(), lambda *_: None)
        self.assertTrue(result["complete"])
        messages = self.requests[1]["body"]["messages"]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-2]["tool_calls"][0]["id"], messages[-1]["tool_call_id"])

    def test_verifier_uses_its_own_strict_function_schema(self):
        from schemas import parse_verdict
        self.mode = "verification"
        model = self.model()
        model.configure_verification()
        result = parse_verdict(model([], time.monotonic() + 5))
        self.assertEqual(result["verdict"], "uncertain")
        function = self.requests[0]["body"]["tools"][0]["function"]
        self.assertEqual(function["name"], "verify_candidate")
        self.assertNotIn("step", function["parameters"]["properties"])
        self.assertIn("counterevidence", function["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
