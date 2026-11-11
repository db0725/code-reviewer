"""只读 Git 已提交内容，生成有大小限制的内存快照；不读取未提交代码或运行项目。"""
import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath
from resource_policy import ContextError, matching_rule, normalize_context, resource_kind


class GitInputError(ValueError):
    pass


SUFFIXES = {".py", ".md", ".rst", ".txt", ".sh"}
DATA_SUFFIXES = {".csv", ".tsv", ".jsonl", ".parquet", ".npy", ".npz", ".h5", ".hdf5", ".nii", ".dcm", ".pt", ".pth", ".ckpt"}
DATA_DIRS = {"data", "dataset", "datasets", "images", "annotations", "weights", "checkpoints", "outputs"}

# 本地快照的读取预算，不等于单次发送给模型的上下文大小。
MAX_SNAPSHOT_FILES = 300
MAX_FILE_BYTES = 256_000
MAX_SNAPSHOT_BYTES = 2_000_000


def git(repo, *arguments, input_data=None):
    environment = dict(os.environ)
    # Do not inherit repository redirection from the invoking shell.
    for name in list(environment):
        if name.startswith("GIT_"):
            environment.pop(name)
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")
    try:
        result = subprocess.run(["git", "-C", str(repo), *arguments], env=environment,
                                input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise GitInputError("无法读取 Git 仓库，请检查路径和 Git 安装。") from None
    if result.returncode:
        raise GitInputError("Git 只读查询失败，请检查仓库路径和提交标识。")
    return result.stdout


def blob_stream(repo, candidates):
    """每次批量读取最多20个已筛选对象，避免为每个文件启动一次 Git。"""
    for start in range(0, len(candidates), 20):
        group = candidates[start:start + 20]
        raw = git(repo, "cat-file", "--batch", input_data=("\n".join(item[1] for item in group) + "\n").encode("ascii"))
        position = 0
        for path, object_id, size in group:
            end = raw.find(b"\n", position)
            expected = ("%s blob %d" % (object_id, size)).encode("ascii")
            if end < 0 or raw[position:end] != expected:
                raise GitInputError("Git 批量读取的对象信息不一致，未启动模型。")
            content = raw[end + 1:end + 1 + size]
            if len(content) != size or raw[end + 1 + size:end + 2 + size] != b"\n":
                raise GitInputError("Git 批量读取内容不完整，未启动模型。")
            yield path, object_id, content
            position = end + 2 + size
        if position != len(raw):
            raise GitInputError("Git 批量读取返回额外内容，未启动模型。")


def resolve_commit(repo, reference):
    """将 HEAD、分支或提交标识固定成完整提交编号，后续不再依赖可变分支名。"""
    if not isinstance(reference, str) or not reference or reference.startswith("-") or "\x00" in reference:
        raise GitInputError("提交标识不合法。")
    return git(repo, "rev-parse", "--verify", "--end-of-options", reference + "^{commit}").decode("ascii").strip()


def snapshot(repo, commit, max_files=MAX_SNAPSHOT_FILES, max_file_bytes=MAX_FILE_BYTES, max_total_bytes=MAX_SNAPSHOT_BYTES, context=None):
    """先看 Git 文件清单，筛选后才读取内容；同时返回排除原因和文件校验信息。"""
    context = normalize_context(context)
    contents, excluded, manifest = {}, [], []
    candidates = []
    total = 0
    for record in git(repo, "ls-tree", "--full-tree", "-r", "-l", "-z", commit).split(b"\x00"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, kind, object_id, size = header.split()
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            excluded.append({"path": repr(raw_path), "reason": "文件名不是 UTF-8"})
            continue
        parts = PurePosixPath(path).parts
        safe = (path and not path.startswith("/") and not any(part in (".", "..") for part in parts)
                and ":" not in path and "\\" not in path and PurePosixPath(path).as_posix() == path)
        reason = None
        if not safe:
            reason = "路径不受支持"
        elif mode not in (b"100644", b"100755") or kind != b"blob":
            reason = "跳过符号链接或子模块"
        elif matching_rule(path, context["excluded_paths"]):
            reason = "用户排除规则：" + matching_rule(path, context["excluded_paths"])
        elif PurePosixPath(path).suffix.lower() in DATA_SUFFIXES or (
                any(part.lower() in DATA_DIRS for part in parts[:-1]) and PurePosixPath(path).suffix.lower() != ".py"):
            reason = "数据集或训练产物不纳入审查"
        elif any(part.lower().startswith(".env") for part in parts) or PurePosixPath(path).suffix.lower() not in SUFFIXES:
            reason = "不在本版文本类型白名单"
        if reason:
            excluded.append({"path": path, "reason": reason, "resource_type": resource_kind(path) or "file"})
            continue
        # 排除项不读取内容、也不占加载预算；超限明确停止，不截断代码。
        length = int(size)
        if length > max_file_bytes:
            raise GitInputError("读取 %s 时超出单文件预算（%s 字节，上限 %s），模型未启动。" % (path, format(length, ","), format(max_file_bytes, ",")))
        candidates.append((path, object_id.decode("ascii"), length))
    for path, object_id, raw in blob_stream(repo, candidates):
        length = len(raw)
        if length > max_file_bytes or total + length > max_total_bytes or len(contents) >= max_files:
            limits = []
            if length > max_file_bytes:
                limits.append("单文件 %s 字节，上限 %s" % (format(length, ","), format(max_file_bytes, ",")))
            if total + length > max_total_bytes:
                limits.append("累计 %s 字节，上限 %s" % (format(total + length, ","), format(max_total_bytes, ",")))
            if len(contents) >= max_files:
                limits.append("文件数将超过 %d 个" % max_files)
            raise GitInputError("读取 %s 时超出预算（%s）。请在额外排除路径中缩小范围后重新预览；代码未截断，模型未启动。" % (path, "；".join(limits)))
        try:
            if b"\x00" in raw:
                raise UnicodeError()
            text = raw.decode("utf-8")
        except UnicodeError:
            excluded.append({"path": path, "reason": "二进制或非 UTF-8 文本"})
            continue
        contents[path] = text
        total += length
        manifest.append({"path": path, "git_blob": object_id, "bytes": length,
                         "sha256": hashlib.sha256(raw).hexdigest()})
    return contents, {"files": manifest, "excluded": excluded, "bytes": total}


def load_git_input(repo, head="HEAD", base=None, context=None):
    """把 Git 输入转换成内置示例的 before/after 结构，复用同一个 Agent。"""
    try:
        context = normalize_context(context)
    except ContextError as error:
        raise GitInputError(str(error)) from None
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise GitInputError("仓库目录不存在。")
    head_id = resolve_commit(repo, head)
    base_id = resolve_commit(repo, base) if base is not None else None
    if base_id == head_id:
        raise GitInputError("前后提交相同，没有可审查的代码差异。")
    after, after_scope = snapshot(repo, head_id, context=context)
    before, before_scope = snapshot(repo, base_id, context=context) if base_id else ({}, {"files": [], "excluded": [], "bytes": 0})
    removed = sorted(set(before) - set(after))
    if removed:
        raise GitInputError("本版尚不支持已删除文本文件的证据锚定，未启动模型。")
    changed = [path for path in after if before.get(path) != after[path]]
    if not changed:
        raise GitInputError("白名单范围内没有可审查的文本差异，未启动模型。")
    metadata = {"repository": str(repo), "base_commit": base_id, "head_commit": head_id,
                "comparison": "initial-import" if base_id is None else "two-commits",
                "before_scope": before_scope, "after_scope": after_scope, "changed_files": changed,
                "read_policy": "Committed code/docs only; datasets excluded; no checkout, fetch, execution or worktree reads.",
                "datasets_excluded": True, "project_context": context}
    example = {"id": "git-" + head_id[:12], "title": repo.name + " · 本地 Git 审查", "tag": "本地 Git",
               "description": ("初次导入代码，修改前视为空快照。" if base_id is None else "比较两个固定提交的代码变更。") +
                              "只根据代码报告有具体触发条件的功能问题，最多报告三项最重要的问题。"
                              "不评论排版、许可证或仅影响文档链接的问题；不推断医学性能。"
                              "数据集与权重不纳入审查；这是静态审查，没有执行项目或测试。",
               "before": before, "after": after, "project_context": context,
               "excluded_resources": {"before": before_scope["excluded"], "after": after_scope["excluded"]}}
    return example, metadata


def inspect_repository(repo):
    path = Path(repo).resolve()
    if not path.is_dir():
        raise GitInputError("仓库目录不存在。")
    # Normalize a selected subdirectory to the repository root. Do not walk it.
    root = Path(git(path, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    head = resolve_commit(root, "HEAD")
    raw = git(root, "log", "-30", "--format=%H%x00%cs%x00%s%x00", head).decode("utf-8", errors="replace")
    fields = raw.split("\x00")
    commits = [{"id": fields[i].strip(), "date": fields[i + 1], "subject": fields[i + 2]}
               for i in range(0, len(fields) - 2, 3)]
    return {"repository": str(root), "name": root.name, "head": head, "commits": commits,
            "datasets_excluded": True, "note": "只读 Git 提交中的代码与说明，不扫描本地数据集。"}


def scope_markdown(metadata):
    excluded = metadata["after_scope"]["excluded"]
    return ("\n\n## 本地 Git 审查范围\n\n"
            "仓库：%s\n\n比较方式：%s\n\n修改前：%s\n\n修改后：%s\n\n"
            "加载 %d 个文本文件（%d 字节），变更 %d 个文件，排除 %d 个条目。\n\n"
            "数据集、权重、未提交文件不纳入审查；只读固定提交，未执行项目或测试。\n") % (
                metadata["repository"], metadata["comparison"], metadata["base_commit"] or "空快照（首次导入）",
                metadata["head_commit"], len(metadata["after_scope"]["files"]), metadata["after_scope"]["bytes"],
                len(metadata["changed_files"]), len(excluded))
