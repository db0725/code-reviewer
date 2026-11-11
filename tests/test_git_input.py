import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git_input import GitInputError, blob_stream, inspect_repository, load_git_input, snapshot
from review import CodeTools


class GitInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.command("init", "-q")
        self.command("config", "user.name", "Local Test")
        self.command("config", "user.email", "local@example.invalid")
        (self.repo / "main.py").write_text("def total(value):\n    return value\n", encoding="utf-8")
        (self.repo / "README.md").write_text("Local fixture\n", encoding="utf-8")
        (self.repo / ".env").write_text("DO_NOT_SEND=private-test-value\n", encoding="utf-8")
        (self.repo / "image.bin").write_bytes(b"binary")
        self.command("add", ".")
        self.command("commit", "-qm", "base")
        self.base = self.command("rev-parse", "HEAD").strip()
        (self.repo / "main.py").write_text("def total(value):\n    return value + 1\n", encoding="utf-8")
        self.command("add", "main.py")
        self.command("commit", "-qm", "head")
        self.head = self.command("rev-parse", "HEAD").strip()

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode("utf-8")

    def test_snapshot_uses_committed_blobs_and_keeps_worktree_unchanged(self):
        (self.repo / "main.py").write_text("uncommitted work\n", encoding="utf-8")
        status = self.command("status", "--porcelain")
        example, metadata = load_git_input(self.repo, base=self.base)
        self.assertIn("return value\n", example["before"]["main.py"])
        self.assertIn("return value + 1", example["after"]["main.py"])
        self.assertNotIn("uncommitted", json.dumps(example))
        self.assertEqual(metadata["head_commit"], self.head)
        self.assertEqual(self.command("status", "--porcelain"), status)
        self.assertEqual((self.repo / "main.py").read_text(), "uncommitted work\n")

    def test_initial_import_and_secret_binary_exclusions(self):
        example, metadata = load_git_input(self.repo)
        self.assertEqual(example["before"], {})
        self.assertIsNone(metadata["base_commit"])
        self.assertNotIn("private-test-value", json.dumps(example))
        self.assertEqual({x["path"] for x in metadata["after_scope"]["excluded"]}, {".env", "image.bin"})

    def test_tools_search_and_ast_work_on_git_snapshots(self):
        example, _ = load_git_input(self.repo, base=self.base)
        tools = CodeTools(example)
        self.assertTrue(tools.execute("read_file", {"path": "main.py", "version": "before"})["ok"])
        self.assertEqual(tools.execute("find_definition", {"symbol": "total"})["result"]["definitions"][0]["line"], 1)
        self.assertTrue(tools.execute("search_code", {"query": "value + 1"})["result"]["matches"])

    def test_invalid_refs_path_same_commit_and_injected_option(self):
        for kwargs in ({"base": "unknown-commit"}, {"base": self.head}, {"head": "--help"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(GitInputError):
                load_git_input(self.repo, **kwargs)
        with self.assertRaises(GitInputError):
            load_git_input(self.repo / "missing")

    def test_budget_rejects_instead_of_silently_truncating(self):
        with self.assertRaises(GitInputError):
            snapshot(self.repo, self.head, max_total_bytes=1)

    def test_batch_blob_framing_rejects_mismatch_truncation_and_extra_bytes(self):
        object_id = "a" * 40
        good = (object_id + " blob 3\n").encode() + b"x\ny\n"
        with patch("git_input.git", return_value=good):
            self.assertEqual(list(blob_stream(self.repo, [("main.py", object_id, 3)]))[0][2], b"x\ny")
        for bad in (good[:-2], good + b"extra", good.replace(b"blob 3", b"blob 4")):
            with patch("git_input.git", return_value=bad), self.assertRaises(GitInputError):
                list(blob_stream(self.repo, [("main.py", object_id, 3)]))

    def test_deleted_file_is_explicitly_unsupported(self):
        (self.repo / "main.py").unlink()
        self.command("add", "-u")
        self.command("commit", "-qm", "delete")
        with self.assertRaisesRegex(GitInputError, "已删除"):
            load_git_input(self.repo, base=self.base)

    def test_symlink_blob_is_not_loaded_or_followed(self):
        # Simulate a committed symlink without relying on Windows symlink privileges.
        tree = b"120000 blob " + b"a" * 40 + b"       12\tlinked.py\x00"
        with patch("git_input.git", return_value=tree) as command:
            files, scope = snapshot(self.repo, self.head)
        self.assertEqual(files, {})
        self.assertIn("符号链接", scope["excluded"][0]["reason"])
        self.assertEqual(command.call_count, 1)

    def test_large_tracked_dataset_and_untracked_data_are_not_loaded(self):
        (self.repo / "data").mkdir()
        (self.repo / "data" / "large.csv").write_text("PRIVATE_DATASET\n" * 15000, encoding="utf-8")
        (self.repo / "data" / "loader.py").write_text("def loader():\n    return []\n", encoding="utf-8")
        self.command("add", "data")
        self.command("commit", "-qm", "tracked dataset")
        (self.repo / "data" / "local-only.csv").write_text("UNTRACKED_DATASET", encoding="utf-8")
        example, source = load_git_input(self.repo)
        self.assertNotIn("PRIVATE_DATASET", json.dumps(example))
        self.assertNotIn("UNTRACKED_DATASET", json.dumps(example))
        self.assertNotIn("data/local-only.csv", json.dumps(source))
        self.assertIn("data/loader.py", example["after"])
        self.assertTrue(source["datasets_excluded"])
        self.assertIn("data/large.csv", {item["path"] for item in source["after_scope"]["excluded"]})

    def test_inspect_subdirectory_returns_repository_root_and_fixed_commits(self):
        (self.repo / "nested").mkdir()
        info = inspect_repository(self.repo / "nested")
        self.assertEqual(Path(info["repository"]), self.repo.resolve())
        self.assertEqual([item["id"] for item in info["commits"]], [self.head, self.base])
        example, _ = load_git_input(self.repo / "nested", base=self.base)
        self.assertIn("main.py", example["after"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
