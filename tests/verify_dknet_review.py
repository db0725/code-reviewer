"""Controlled reproductions of two findings; does not import DKNet or Torch."""
import argparse
import ast
import hashlib
import json
import os
import sys
import types
from pathlib import Path

from git_input import resolve_commit, snapshot


def extract(source, name, class_name=None):
    tree = ast.parse(source)
    nodes = tree.body
    if class_name:
        nodes = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name).body
    node = next(node for node in nodes if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    return compile(ast.fix_missing_locations(module), "<pinned-DKNet-function>", "exec")


def reproduce(files):
    checks = []
    namespace = {"os": os, "cv2": types.SimpleNamespace(imread=lambda _: None),
                 "np": types.SimpleNamespace(random=types.SimpleNamespace(randint=lambda low, high: 0))}
    exec(extract(files["dknet/data.py"], "resolve_path"), namespace)
    exec(extract(files["dknet/data.py"], "__getitem__", "UnlabeledDataset"), namespace)

    class Rows(list):
        @property
        def iloc(self):
            return self

    class DatasetFixture:
        __getitem__ = namespace["__getitem__"]
        data = Rows([{"Path": "missing.png"}])
        root_dir = "unused-root"

    previous_limit = sys.getrecursionlimit()
    caught = None
    try:
        sys.setrecursionlimit(160)
        DatasetFixture()[0]
    except RecursionError as error:
        caught = type(error).__name__
    finally:
        sys.setrecursionlimit(previous_limit)
    assert caught == "RecursionError", "Missing-image branch did not reproduce"
    checks.append({"finding": 1, "verification": "confirmed-controlled-branch", "observed": caught,
                   "condition": "One unlabeled row; imread always returns None; the only retry index is zero.",
                   "limit": "Original method body with controlled I/O substitutes; no actual OpenCV, images or training."})

    namespace = {}
    exec(extract(files["dknet/training.py"], "infinite_loader"), namespace)
    cause = None
    try:
        next(namespace["infinite_loader"]([]))
    except RuntimeError as error:
        cause = type(error.__cause__).__name__
    assert cause == "StopIteration", "Empty-loader branch did not reproduce"
    iterator = namespace["infinite_loader"]([1, 2])
    assert [next(iterator) for _ in range(4)] == [1, 2, 1, 2]
    checks.append({"finding": 2, "verification": "confirmed-empty-iterator", "observed": "RuntimeError: generator raised StopIteration",
                   "cause": cause, "nonempty_control": [1, 2, 1, 2],
                   "correction": "The exception seen by the caller is RuntimeError with StopIteration as its cause.",
                   "limit": "Real helper body with an empty iterable. Torch DataLoader and the training pipeline were not run."})
    checks.append({"finding": 3, "verification": "partially-supported-static-only",
                   "observed": "The evaluation call does not inspect the incompatible-key result of load_state_dict.",
                   "correction": "The broad claim about all configuration mismatches leaving random parameters is not established. BN/GN replacement preserves affine parameter names; conditional module differences need separate checking.",
                   "limit": "No Torch/checkpoint execution. This is a configuration-consistency risk, not a fully reproduced model-loading bug."})
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists():
        parser.error("验证记录已存在，请使用新的输出文件。")
    commit = resolve_commit(args.repo, args.commit)
    files, _ = snapshot(args.repo, commit)
    report = {"commit": commit, "method": "Pinned source AST extraction; controlled branches, not end-to-end execution.",
              "source_sha256": {name: hashlib.sha256(files[name].encode("utf-8")).hexdigest()
                                for name in ("dknet/data.py", "dknet/training.py", "evaluate.py")},
              "checks": reproduce(files)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
