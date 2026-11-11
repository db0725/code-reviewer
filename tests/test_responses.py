import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from review import DeepSeekModel, ModelError, Settings, parse_action


class ResponsesAdapterTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.mode = "success"
        owner = self
        class Stub(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.received.append({"path": self.path, "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                action = {"action": "final", "summary": "结构测试", "findings": []}
                content = [{"type": "output_text", "text": json.dumps({"step": action})}]
                if owner.mode == "multiple":
                    content.append(dict(content[0]))
                elif owner.mode == "refusal":
                    content = [{"type": "refusal", "refusal": "not echoed"}]
                elif owner.mode == "empty":
                    content = []
                payload = {"status": "incomplete" if owner.mode == "incomplete" else "completed",
                           "incomplete_details": {"reason": "max_output_tokens"} if owner.mode == "incomplete" else None,
                           "output": [{"type": "message", "status": "completed", "content": content}],
                           "usage": {"input_tokens": 120, "output_tokens": 30}}
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

    def model(self):
        return DeepSeekModel(Settings(api_key="local-placeholder", base_url="http://127.0.0.1:%s" % self.server.server_port), protocol="responses")

    def test_native_schema_endpoint_and_token_accounting(self):
        model = self.model()
        parsed = parse_action(model([{"role": "system", "content": "JSON"}], time.monotonic() + 5), envelope=True)
        self.assertEqual(parsed["action"], "final")
        request = self.received[0]
        self.assertEqual(request["path"], "/responses")
        self.assertEqual(request["body"]["text"]["format"]["type"], "json_schema")
        self.assertIs(request["body"]["text"]["format"]["strict"], True)
        self.assertNotIn("messages", request["body"])
        self.assertEqual(model.usage, {"prompt_tokens": 120, "completion_tokens": 30})
        self.assertNotIn("local-placeholder", json.dumps(request))

    def test_diff_only_requests_final_only_schema(self):
        model = self.model()
        model.configure(False)
        model([], time.monotonic() + 5)
        schema = self.received[0]["body"]["text"]["format"]["schema"]
        self.assertEqual(schema["properties"]["step"]["properties"]["action"]["enum"], ["final"])
        self.assertNotIn("anyOf", schema["properties"]["step"])

    def test_incomplete_multiple_refusal_and_empty_are_not_executable(self):
        for mode in ("incomplete", "multiple", "refusal", "empty"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(ModelError):
                self.model()([], time.monotonic() + 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
