"""变更分批与轻量代码检索。只处理内存快照，不导入或执行仓库代码。"""
import ast
import difflib
import json
import math
import re
from collections import Counter
from pathlib import PurePosixPath


UNIT_CHARS = 20_000
WINDOW_ROWS = 80
CODE_SUFFIXES = {".py", ".sh"}


def compact_changes(unit):
    """行号信息保留在紧凑 diff 中，避免逐行重复传输 JSON 字段名。"""
    markers = {"add": "+", "delete": "-", "context": " "}
    return [{"path": change["path"], "diff": "\n".join(
        "%s%s:%s | %s" % (markers[row["kind"]], row["old"] if row["old"] is not None else "-",
                          row["new"] if row["new"] is not None else "-", row["text"])
        for row in change["rows"])} for change in unit["changes"]]


def review_plan(example, unit_chars=UNIT_CHARS):
    """按变更附近的行分批，保留真实新旧行号；过长行明确标为未覆盖。"""
    units, pending, context_only = [], [], []
    current = {"files": [], "changes": []}

    def flush():
        nonlocal current
        if current["changes"]:
            current["id"] = "batch-%02d" % (len(units) + 1)
            units.append(current)
        current = {"files": [], "changes": []}

    def append_window(path, rows):
        change = {"path": path, "rows": rows}
        proposed = dict(current, changes=current["changes"] + [change])
        if current["changes"] and (len(set(current["files"] + [path])) > 5 or
                                   len(json.dumps(compact_changes(proposed), ensure_ascii=False)) > unit_chars):
            flush()
        if path not in current["files"]:
            current["files"].append(path)
        current["changes"].append(change)

    for path, after in example["after"].items():
        before = example["before"].get(path, "")
        if before == after:
            continue
        if PurePosixPath(path).suffix.lower() not in CODE_SUFFIXES:
            context_only.append(path)
            continue
        old, new = before.splitlines(), after.splitlines()
        matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
        for group in matcher.get_grouped_opcodes(3):
            rows = []
            for tag, a, b, c, d in group:
                if tag == "equal":
                    rows.extend({"kind": "context", "old": i + 1, "new": j + 1, "text": old[i]}
                                for i, j in zip(range(a, b), range(c, d)))
                else:
                    rows.extend({"kind": "delete", "old": i + 1, "new": None, "text": old[i]} for i in range(a, b))
                    rows.extend({"kind": "add", "old": None, "new": j + 1, "text": new[j]} for j in range(c, d))
            window = []
            for row in rows:
                if len(json.dumps(row, ensure_ascii=False)) > unit_chars // 2:
                    pending.append({"path": path, "line": row["new"] or row["old"], "reason": "变更行过长，未截断后假装覆盖"})
                    continue
                proposed = {"path": path, "rows": window + [row]}
                if window and (len(window) >= WINDOW_ROWS or len(json.dumps(proposed, ensure_ascii=False)) > unit_chars // 2):
                    append_window(path, window)
                    window = []
                window.append(row)
            if window:
                append_window(path, window)
    flush()
    return {"units": units, "uncovered": pending, "context_only_files": context_only,
            "code_files": sorted({path for unit in units for path in unit["files"]} | {item["path"] for item in pending})}


def code_window(text, start=1, end=None, max_lines=80, max_chars=6000):
    """只返回完整行；省略部分用元数据明确表达，避免伪造行号或半行证据。"""
    lines = text.splitlines()
    if type(start) is not int or start < 1 or (lines and start > len(lines)):
        raise ValueError("start_line 超出文件范围")
    if end is not None and (type(end) is not int or end < start):
        raise ValueError("end_line 必须是不小于 start_line 的整数")
    stop = min(len(lines), end if end is not None else start + max_lines - 1, start + max_lines - 1)
    shown, output, omitted = [], [], []
    length = 0
    for number in range(start, stop + 1):
        row = "%d | %s" % (number, lines[number - 1])
        if length + len(row) > max_chars:
            omitted.append(number)
            continue
        output.append(row)
        shown.append(number)
        length += len(row) + 1
    return {"start_line": start, "end_line": stop, "total_lines": len(lines),
            "shown_lines": shown, "omitted_lines": omitted, "content": "\n".join(output),
            "truncated": bool(omitted) or stop < len(lines), "next_line": stop + 1 if stop < len(lines) else None}


STOP_WORDS = {"def", "return", "self", "import", "from", "none", "true", "false", "if", "else", "and", "or"}


def terms(text):
    words = re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*|[\u4e00-\u9fff]{1,}", text.lower())
    return [part for word in words for part in dict.fromkeys([word, *word.split("_")]) if len(part) > 1 and part not in STOP_WORDS]


class CodeIndex:
    """BM25 片段检索 + AST 定义索引。无需向量数据库，结果仍需代码证据校验。"""

    def __init__(self, files):
        self.files = files
        self.documents, self.definitions = [], {}
        self.df = Counter()
        for path, text in files.items():
            lines = text.splitlines()
            for start in range(1, len(lines) + 1, 52):
                window = code_window(text, start, start + 59, max_lines=60, max_chars=4000)
                counts = Counter(terms(path + "\n" + window["content"]))
                self.documents.append((path, window, counts, sum(counts.values())))
                self.df.update(counts.keys())
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.definitions.setdefault(node.name, []).append((path, node.lineno, node.end_lineno))
        self.avg_length = sum(item[3] for item in self.documents) / max(1, len(self.documents))

    def search(self, query, limit=4):
        scores = []
        total = len(self.documents)
        query_terms = set(terms(query))
        for path, window, counts, length in self.documents:
            score = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if frequency:
                    idf = math.log(1 + (total - self.df[term] + .5) / (self.df[term] + .5))
                    score += idf * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * length / max(1, self.avg_length)))
            if query.casefold() in window["content"].casefold():
                score += 5
            if score:
                scores.append((score, path, window))
        scores.sort(key=lambda item: (-item[0], item[1], item[2]["start_line"]))
        return [dict(window, file=path, line=window["start_line"], score=round(score, 3)) for score, path, window in scores[:limit]]

    def find(self, symbol):
        return [dict(code_window(self.files[path], start, end, max_chars=5000), file=path, line=start)
                for path, start, end in self.definitions.get(symbol, [])[:4]]


class EvidenceLedger:
    """记录真正展示给当前批次模型的代码行，防止引用未查阅的关联代码。"""

    def __init__(self, example, unit):
        self.example, self.visible = example, {}
        for change in unit["changes"]:
            self.add(change["path"], [row["new"] for row in change["rows"] if row["new"] is not None])

    def add(self, path, lines, version="after"):
        if version == "after":
            self.visible.setdefault(path, set()).update(lines)

    def validate(self, finding):
        for reference in [finding, *finding["references"]]:
            path, line = reference["file"], reference["line"]
            source = self.example["after"][path].splitlines()
            excerpt = reference["evidence"].strip("\n").splitlines()
            starts = [start for start in range(len(source) - len(excerpt) + 1)
                      if start + 1 <= line <= start + len(excerpt) and
                      all(source[start + offset].strip() == text.strip() for offset, text in enumerate(excerpt))]
            if not any(set(range(start + 1, start + len(excerpt) + 1)) <= self.visible.get(path, set()) for start in starts):
                raise ValueError("%s:%d 的证据未完整展示给本批模型，请先按行读取再引用" % (path, line))

