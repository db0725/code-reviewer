"""项目说明、资源声明与排除规则的统一定义；本模块不访问数据集或权重文件。"""
import fnmatch
from pathlib import PurePosixPath
from typing import Annotated, List, Literal

from pydantic import Field, ValidationError, field_validator
from schemas import StrictModel


AVAILABILITY_LABELS = {"provided": "用户声明已提供（未验证）", "not_provided": "用户声明未提供", "unknown": "未说明／不确定"}
STATUS_LABELS = {"not_provided": "按声明跳过", "not_checked": "静态审查不读取", "excluded": "按范围排除",
                 "excluded_by_user": "按用户规则排除", "not_in_snapshot": "当前提交快照中未找到"}
WEIGHT_SUFFIXES = {".pt", ".pth", ".ckpt", ".safetensors", ".onnx"}
DATASET_SUFFIXES = {".csv", ".tsv", ".jsonl", ".parquet", ".npy", ".npz", ".h5", ".hdf5", ".nii", ".dcm",
                    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
DATASET_DIRS = {"data", "dataset", "datasets", "images", "annotations"}
WEIGHT_DIRS = {"weights", "checkpoints"}


class ContextError(ValueError):
    pass


class ProjectContext(StrictModel):
    notes: Annotated[str, Field(max_length=2000)] = ""
    dataset: Literal["provided", "not_provided", "unknown"] = "unknown"
    weights: Literal["provided", "not_provided", "unknown"] = "unknown"
    excluded_paths: Annotated[List[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=20)] = Field(default_factory=list)

    @field_validator("excluded_paths")
    @classmethod
    def relative_rules(cls, values):
        result = []
        for value in values:
            rule = value.strip().replace("\\", "/")
            if (not rule or rule.startswith(("/", "!")) or ":" in rule or any(c in rule for c in "\x00\r\n\t")
                    or any(part in (".", "..") for part in rule.split("/"))):
                raise ValueError("排除规则必须是相对路径或通配符，不支持上级路径和反向规则")
            if rule not in result:
                result.append(rule)
        return result


def normalize_context(value=None):
    try:
        return ProjectContext.model_validate({} if value is None else value, strict=True).model_dump(mode="json")
    except ValidationError as error:
        labels = {"notes": "项目说明", "dataset": "数据集状态", "weights": "模型权重状态", "excluded_paths": "额外排除路径"}
        fields = list(dict.fromkeys(labels.get(item["loc"][0], "配置字段") for item in error.errors(include_input=False) if item["loc"]))
        raise ContextError("请检查%s：说明最多 2000 字；状态须从选项选择；排除项最多 20 条，须为相对路径，不支持上级路径或 ! 反向规则。" % "、".join(fields)) from None


def matching_rule(path, rules):
    """支持精确路径、目录前缀和通配符；不是完整的 .gitignore 解析器。"""
    for rule in rules:
        prefix = rule.rstrip("/")
        if path == prefix or path.startswith(prefix + "/") or fnmatch.fnmatchcase(path, rule):
            return rule
    return None


def resource_kind(path):
    value = PurePosixPath(path)
    suffix = value.suffix.lower()
    # data/ 或 models/ 中的 Python 文件仍是源代码，不因目录名而自动跳过。
    if suffix == ".py":
        return None
    parents = {part.lower() for part in value.parts[:-1]}
    if suffix in WEIGHT_SUFFIXES or parents & WEIGHT_DIRS:
        return "weights"
    if suffix in DATASET_SUFFIXES or path.lower().endswith(".nii.gz") or parents & DATASET_DIRS:
        return "dataset"
    return None


def context_markdown(context, resources=()):
    context = normalize_context(context)
    lines = ["", "## 项目说明与验证范围", "", "审查方式：静态代码审查；未执行项目、训练或推理。", "",
             "数据集：%s；未验证图片、样本或数据集完整性。" % AVAILABILITY_LABELS[context["dataset"]], "",
             "模型权重：%s；未验证权重加载或推理结果。" % AVAILABILITY_LABELS[context["weights"]], ""]
    if context["notes"]:
        lines.extend(["用户补充说明（背景信息，不作为缺陷已经成立的证据）：", "", context["notes"], ""])
    if context["excluded_paths"]:
        lines.extend(["用户排除规则：" + "、".join(context["excluded_paths"]), ""])
    lines.extend(["### 不可用资源记录", ""])
    if not resources:
        lines.append("没有额外的资源访问失败记录；不表示数据集或权重已经过验证。")
    for item in resources:
        lines.append("- %s [%s]：%s。%s 请求 %d 次，重复拦截 %d 次。" % (
            item["path"], item["version"], STATUS_LABELS.get(item["status"], item["status"]),
            item["reason"], item["requests"], item["blocked_repeats"]))
    return "\n".join(lines) + "\n"
