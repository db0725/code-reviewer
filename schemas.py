"""用同一套 Pydantic 定义生成远端约束，并在本机严格校验模型动作与报告。"""
import copy
from typing import Annotated, List, Literal, Optional, Union

try:
    from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
except ImportError:
    raise ImportError("缺少 Pydantic。请使用项目 .venv 的 Python，或先运行 setup.ps1 安装依赖。") from None


Text = Annotated[str, Field(min_length=1, max_length=3000, pattern=r"\S")]
PathText = Annotated[str, Field(min_length=1, max_length=300, pattern=r"\S")]


class StrictModel(BaseModel):
    # 不接受额外字段，也不把字符串 "6" 自动转换为整数行号 6。
    model_config = ConfigDict(strict=True, extra="forbid")


class EmptyArgs(StrictModel):
    pass


class ReadFileArgs(StrictModel):
    path: PathText
    version: Literal["before", "after"] = "after"
    start_line: Optional[Annotated[int, Field(ge=1)]] = None
    end_line: Optional[Annotated[int, Field(ge=1)]] = None


class SearchArgs(StrictModel):
    query: Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]


class DefinitionArgs(StrictModel):
    symbol: Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]


class Reference(StrictModel):
    file: PathText
    line: Annotated[int, Field(ge=1)]
    evidence: Text


class Finding(Reference):
    severity: Literal["high", "medium", "low"]
    title: Text
    trigger: Text
    explanation: Text
    suggestion: Text
    references: Annotated[List[Reference], Field(max_length=8)]


Findings = Annotated[List[Finding], Field(max_length=20)]


class GetDiffAction(StrictModel):
    action: Literal["get_diff"]
    arguments: EmptyArgs


class ReadFileAction(StrictModel):
    action: Literal["read_file"]
    arguments: ReadFileArgs


class SearchAction(StrictModel):
    action: Literal["search_code"]
    arguments: SearchArgs


class DefinitionAction(StrictModel):
    action: Literal["find_definition"]
    arguments: DefinitionArgs


class FinalReport(StrictModel):
    action: Literal["final"]
    summary: Text
    findings: Findings


# 根据 action 选择对应结构：读文件动作不能混入最终报告的字段。
Action = Annotated[Union[GetDiffAction, ReadFileAction, SearchAction, DefinitionAction, FinalReport], Field(discriminator="action")]


class WireGetDiffAction(StrictModel):
    # DeepSeek 不接受没有属性的对象，所以传输时省略空 arguments，本机解析时补回。
    action: Literal["get_diff"]


WireAction = Annotated[Union[WireGetDiffAction, ReadFileAction, SearchAction, DefinitionAction, FinalReport], Field(discriminator="action")]


class ActionEnvelope(StrictModel):
    step: WireAction


class FinalEnvelope(StrictModel):
    step: FinalReport


class VersionedReference(Reference):
    version: Literal["before", "after"]


class CandidateVerdict(StrictModel):
    """复核是独立任务：明确支持、反驳或不确定，不让模型悄悄改写原候选。"""
    candidate_id: Annotated[str, Field(min_length=1, max_length=100)]
    verdict: Literal["supported", "refuted", "uncertain"]
    rationale: Text
    mechanism_assessment: Literal["supported", "contradicted", "unknown"]
    regression_assessment: Literal["supported", "contradicted", "unknown"]
    impact_assessment: Literal["supported", "contradicted", "unknown"]
    supporting_evidence: Annotated[List[VersionedReference], Field(max_length=4)]
    counterevidence: Annotated[List[VersionedReference], Field(max_length=4)]
    limitations: Annotated[List[Text], Field(max_length=6)]


ACTION_ADAPTER = TypeAdapter(Action)
FINDINGS_ADAPTER = TypeAdapter(Findings)


class SchemaError(ValueError):
    pass


def safe_validation_error(error):
    """Expose locations and categories, never the invalid model output itself."""
    messages = []
    for item in error.errors(include_input=False, include_context=False, include_url=False)[:4]:
        location = ".".join(str(part) for part in item["loc"]) or "root"
        messages.append(location + ": " + item["type"])
    return "结构校验失败（" + "; ".join(messages) + "）。请严格填写 Schema；禁止额外字段和类型转换。"


def validate_action_json(raw, envelope=False, allow_tools=True):
    """校验整个 JSON，不从解释文字中截取一段假装成功；统一返回内部动作字典。"""
    try:
        if envelope:
            model = ActionEnvelope if allow_tools else FinalEnvelope
            action = model.model_validate_json(raw, strict=True).step
        elif allow_tools:
            action = ACTION_ADAPTER.validate_json(raw, strict=True)
        else:
            action = FinalReport.model_validate_json(raw, strict=True)
        result = action.model_dump(mode="json", exclude_unset=True)
        if envelope and result["action"] == "get_diff":
            result["arguments"] = {}
        if result["action"] == "read_file":
            # 远端严格 Schema 要求 nullable 字段也出现；内部统一省略未指定的行号。
            for name in ("start_line", "end_line"):
                if result["arguments"].get(name) is None:
                    result["arguments"].pop(name, None)
        return result
    except ValidationError as error:
        raise SchemaError(safe_validation_error(error)) from None


def validate_finding_objects(findings):
    try:
        return [item.model_dump(mode="json") for item in FINDINGS_ADAPTER.validate_python(findings, strict=True)]
    except ValidationError as error:
        raise SchemaError(safe_validation_error(error)) from None


def wire_action(action):
    return {"action": "get_diff"} if action["action"] == "get_diff" else action


def provider_schema(allow_tools=True):
    """展开引用并生成模型接口支持的 Schema 子集。

    远端限制结构、枚举与动作分支；字符长度、数字范围等仍由本机 Pydantic 检查。
    各分支 action 不重叠，因此这里可以用 anyOf 表示原来的 oneOf。
    """
    return _provider_model_schema(ActionEnvelope if allow_tools else FinalEnvelope)


def verifier_schema():
    return _provider_model_schema(CandidateVerdict)


def parse_verdict(raw):
    try:
        return CandidateVerdict.model_validate_json(raw, strict=True).model_dump(mode="json")
    except ValidationError as error:
        raise SchemaError(safe_validation_error(error)) from None


def _provider_model_schema(model):
    original = model.model_json_schema()
    definitions = original.get("$defs", {})
    local_constraints = {"title", "default", "$defs", "discriminator", "minLength", "maxLength",
                         "minItems", "maxItems", "minimum", "maximum", "pattern"}
    def normalize(value):
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            if not reference.startswith("#/$defs/"):
                raise ValueError("不支持外部 Schema 引用")
            return normalize(copy.deepcopy(definitions[reference.split("/")[-1]]))
        result = {}
        for key, item in value.items():
            if key in local_constraints:
                continue
            if key == "const":
                result["enum"] = [item]
            elif key == "oneOf":
                result["anyOf"] = normalize(item)
            elif key == "properties":
                # Property names such as "title" are data, not schema metadata.
                result[key] = {name: normalize(schema) for name, schema in item.items()}
            else:
                result[key] = normalize(item)
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result
    return normalize(original)
