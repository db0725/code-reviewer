import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from review import Settings
from server import Job, ReviewServer
from tests.test_review import SequenceModel


class GitAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.command("init", "-q")
        self.command("config", "user.name", "Local API Test")
        self.command("config", "user.email", "local@example.invalid")
        (self.repo / "main.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        self.command("add", ".")
        self.command("commit", "-qm", "base")
        self.base = self.command("rev-parse", "HEAD").strip()
        (self.repo / "main.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        (self.repo / "data").mkdir()
        (self.repo / "data" / "records.csv").write_text("private-dataset-content\n", encoding="utf-8")
        self.command("add", ".")
        self.command("commit", "-qm", "head")
        self.head = self.command("rev-parse", "HEAD").strip()
        self.server = ReviewServer(("127.0.0.1", 0), Settings(api_key="test-only-api-key"), model_factory=lambda: SequenceModel([
            {"action": "read_file", "arguments": {"path": "main.py"}},
            {"action": "final", "summary": "已检查固定版本", "findings": []}]))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temporary.cleanup()

    def command(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=True).stdout.decode("utf-8")

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10)

    def preview(self, base=None, context=None):
        body = {"repository": str(self.repo), "base": base, "head": "HEAD"}
        if context is not None:
            body["project_context"] = context
        with self.request("/api/git/preview", body) as response:
            return json.load(response)

    def assert_rejected(self, path, body, code=400, headers=None):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(path, body, headers)
        self.assertEqual(caught.exception.code, code)

    def test_inspect_and_preview_skip_dataset_without_model_call(self):
        with self.request("/api/git/inspect", {"repository": str(self.repo)}) as response:
            info = json.load(response)
        self.assertEqual([item["id"] for item in info["commits"]], [self.head, self.base])
        preview = self.preview(self.base)
        self.assertNotIn("private-dataset-content", json.dumps(preview))
        self.assertEqual(preview["source"]["base_commit"], self.base)
        self.assertTrue(preview["source"]["datasets_excluded"])
        self.assertFalse(self.server.jobs)

    def test_real_task_sse_restore_and_download_keep_preview_commit(self):
        preview = self.preview(self.base)
        (self.repo / "main.py").write_text("def value():\n    return 999\n", encoding="utf-8")
        self.command("add", "main.py")
        self.command("commit", "-qm", "later")
        with self.request("/api/git/reviews", {"preview_id": preview["preview_id"]}) as response:
            job = json.load(response)
        with self.request("/api/reviews/%s/events" % job["id"]) as response:
            events = response.read().decode("utf-8")
        self.assertIn("event: tool", events)
        self.assertIn("event: done", events)
        with self.request("/api/reviews/%s/input" % job["id"]) as response:
            restored = json.load(response)
        self.assertEqual(restored["source"]["head_commit"], self.head)
        self.assertEqual(restored["example"]["files"][0]["after"], "def value():\n    return 2\n")
        self.assertNotIn("return 999", json.dumps(restored))
        with self.request("/api/reviews/%s/report" % job["id"]) as response:
            report = response.read().decode("utf-8")
        self.assertIn(self.base, report)
        self.assertIn(self.head, report)
        self.assertIn("数据集", report)
        self.assertNotIn("test-only-api-key", events + report)

    def test_invalid_repo_versions_preview_and_origin_are_rejected(self):
        self.assert_rejected("/api/git/inspect", {"repository": str(self.repo / "missing")})
        for body in ({"repository": str(self.repo), "head": "missing", "base": None},
                     {"repository": str(self.repo), "head": self.head, "base": self.head},
                     {"repository": str(self.repo), "head": [], "base": None}):
            self.assert_rejected("/api/git/preview", body)
        self.assert_rejected("/api/git/reviews", {"preview_id": "unknown"})
        self.assert_rejected("/api/git/inspect", {"repository": str(self.repo)}, 403, {"Origin": "http://external.example"})
        self.assertFalse(self.server.jobs)

    def test_preview_expiration_requires_new_preview(self):
        preview = self.preview()
        self.server.previews[preview["preview_id"]]["created"] -= 1801
        self.assert_rejected("/api/git/reviews", {"preview_id": preview["preview_id"]})
        self.assertFalse(self.server.jobs)

    def test_missing_key_and_running_job_are_clear_failures(self):
        preview = self.preview()
        self.server.settings.api_key = ""
        self.assert_rejected("/api/git/reviews", {"preview_id": preview["preview_id"]}, 422)
        self.server.settings.api_key = "test-key"
        self.server.jobs["occupied"] = Job("checkout", "real")
        self.assert_rejected("/api/git/reviews", {"preview_id": preview["preview_id"]}, 409)

    def test_project_context_is_frozen_restored_and_in_resource_report(self):
        context = {"notes": "数据和权重需自行准备", "dataset": "not_provided", "weights": "provided", "excluded_paths": ["unused/"]}
        model = SequenceModel([{"action": "read_file", "arguments": {"path": "data/records.csv"}},
                               {"action": "read_file", "arguments": {"path": "data/records.csv"}},
                               {"action": "read_file", "arguments": {"path": "main.py"}},
                               {"action": "final", "summary": "静态检查完成", "findings": []}])
        self.server.model_factory = lambda: model
        preview = self.preview(context=context)
        self.assertEqual(preview["source"]["project_context"]["dataset"], "not_provided")
        context["notes"] = "更改后的说明不应进入旧任务"
        with self.request("/api/git/reviews", {"preview_id": preview["preview_id"]}) as response:
            job = json.load(response)
        with self.request("/api/reviews/%s/events" % job["id"]) as response:
            events = response.read().decode("utf-8")
        self.assertEqual(events.count("event: resource\n"), 1)
        with self.request("/api/reviews/%s" % job["id"]) as response:
            result = json.load(response)["result"]
        self.assertTrue(result["complete"])
        self.assertEqual(result["resource_notes"][0]["blocked_repeats"], 1)
        self.assertEqual(result["verification_scope"]["weights"], "not_verified")
        with self.request("/api/reviews/%s/input" % job["id"]) as response:
            restored = json.load(response)
        self.assertEqual(restored["source"]["project_context"]["notes"], "数据和权重需自行准备")
        with self.request("/api/reviews/%s/report" % job["id"]) as response:
            report = response.read().decode("utf-8")
        self.assertIn("数据和权重需自行准备", report)
        self.assertIn("用户声明已提供（未验证）", report)
        self.assertIn("不可用资源记录", report)
        self.assertNotIn("private-dataset-content", events + report)

    def test_invalid_project_context_is_rejected_before_model_call(self):
        for context in ({"dataset": "maybe"}, {"notes": "x" * 2001}, {"excluded_paths": ["../private"]}):
            self.assert_rejected("/api/git/preview", {"repository": str(self.repo), "base": None, "head": "HEAD", "project_context": context})
        self.assertFalse(self.server.previews)
        self.assertFalse(self.server.jobs)

    def test_known_credentials_are_redacted_from_project_notes(self):
        preview = self.preview(context={"notes": "示例说明 " + self.server.settings.api_key})
        self.assertNotIn(self.server.settings.api_key, json.dumps(preview))
        stored = self.server.previews[preview["preview_id"]]["example"]["project_context"]["notes"]
        self.assertIn("[REDACTED]", stored)


if __name__ == "__main__":
    unittest.main(verbosity=2)
