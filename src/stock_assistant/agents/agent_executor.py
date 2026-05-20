import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from stock_assistant.agents.agent_tools import AgentToolSpec
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.llm_tools import LlmToolCall


SENSITIVE_KEY_PARTS = (
    "cookie",
    "api_key",
    "apikey",
    "authorization",
    "token",
    "source_row",
    "raw_content",
    "account_id",
    "uid",
)


class ToolObservation(BaseModel):
    call_id: str
    tool_name: str
    ok: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error_type: str = ""
    message: str = ""
    summary: str = ""


TECHNICAL_LLM_FIELDS = (
    "ok",
    "latest_date",
    "latest_close",
    "daily_pct_change",
    "ret5_pct",
    "ret20_pct",
    "rsi14",
    "drawdown_from_120d_high_pct",
    "profit_pct",
    "portfolio_weight_pct",
    "technical_observations",
)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                continue
            output[str(key)] = redact_sensitive(item)
        return output
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    return value


def truncate_payload(value: dict[str, Any], max_chars: int) -> dict[str, Any]:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return value
    if isinstance(value.get("content"), str):
        preserved = {
            key: item
            for key, item in value.items()
            if key not in {"content"}
        }
        overhead = len(json.dumps({**preserved, "content": ""}, ensure_ascii=False, default=str))
        content_limit = max(500, max_chars - overhead - 200)
        return {
            **preserved,
            "content": value["content"][:content_limit],
            "truncated": True,
            "original_chars": len(text),
        }
    if isinstance(value.get("results"), list):
        preserved = {
            key: item
            for key, item in value.items()
            if key not in {"results"}
        }
        results: list[Any] = []
        for item in value["results"]:
            candidate = {**preserved, "results": [*results, item], "truncated": True, "original_chars": len(text)}
            if len(json.dumps(candidate, ensure_ascii=False, default=str)) > max_chars:
                break
            results.append(item)
        return {
            **preserved,
            "results": results,
            "truncated": True,
            "original_chars": len(text),
        }
    return {
        "truncated": True,
        "preview": text[:max_chars],
        "original_chars": len(text),
    }


def compact_technical_for_llm(result: dict[str, Any]) -> dict[str, Any]:
    technical = result.get("technical")
    if not isinstance(technical, dict):
        return result
    compacted: dict[str, dict[str, Any]] = {}
    for code, item in technical.items():
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key in TECHNICAL_LLM_FIELDS:
            value = item.get(key)
            if value is None or value == "":
                continue
            row[key] = value
        compacted[str(code)] = row
    return {
        "technical": compacted,
        "summary": result.get("summary") or f"返回 {len(compacted)} 个标的技术指标",
        "llm_compacted": True,
    }


def compact_observation_payload_for_llm(observation: ToolObservation, max_chars: int = 8000) -> dict[str, Any]:
    payload = observation.model_dump()
    if observation.ok and observation.tool_name == "get_holding_technical":
        payload["result"] = compact_technical_for_llm(observation.result or {})

    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return payload

    result = payload.get("result")
    if isinstance(result, dict):
        payload["result"] = truncate_payload(result, max(1000, max_chars - 1200))
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return payload
    return {
        "call_id": payload.get("call_id", ""),
        "tool_name": payload.get("tool_name", ""),
        "ok": payload.get("ok", False),
        "summary": payload.get("summary", ""),
        "error_type": payload.get("error_type", ""),
        "message": payload.get("message", ""),
        "result": {
            "truncated": True,
            "preview": text[:max_chars],
            "original_chars": len(text),
        },
    }


def execute_tool_call(
    call: LlmToolCall,
    registry: dict[str, AgentToolSpec],
    workspace: AgentWorkspace,
    *,
    max_observation_chars: int = 12000,
) -> ToolObservation:
    if call.name not in registry:
        return ToolObservation(
            call_id=call.id,
            tool_name=call.name,
            ok=False,
            error_type="unknown_tool",
            message=f"工具 {call.name} 不在允许列表中",
        )

    tool = registry[call.name]
    if not tool.read_only:
        return ToolObservation(
            call_id=call.id,
            tool_name=call.name,
            ok=False,
            error_type="permission_denied",
            message=f"工具 {call.name} 不是只读工具，已拒绝",
        )

    try:
        args = tool.args_model.model_validate(call.arguments)
    except ValidationError as exc:
        return ToolObservation(
            call_id=call.id,
            tool_name=call.name,
            ok=False,
            error_type="invalid_arguments",
            message=str(exc),
        )

    try:
        result = tool.handler(args, workspace)
    except Exception as exc:  # noqa: BLE001
        return ToolObservation(
            call_id=call.id,
            tool_name=call.name,
            ok=False,
            error_type="tool_error",
            message=str(exc),
        )

    clean_result = truncate_payload(redact_sensitive(result), max_observation_chars)
    return ToolObservation(
        call_id=call.id,
        tool_name=call.name,
        ok=True,
        result=clean_result,
        summary=str(clean_result.get("summary") or f"工具 {call.name} 执行完成"),
    )


def tool_observation_message(
    call: LlmToolCall,
    observation: ToolObservation,
    *,
    compact: bool = True,
) -> dict[str, str]:
    payload = compact_observation_payload_for_llm(observation) if compact else observation.model_dump()
    return {
        "role": "user",
        "content": (
            "工具调用结果 observation。请基于该结果继续决定下一步：继续调用工具，或输出 final_report。\n"
            f"tool_call_id: {call.id}\n"
            f"tool_name: {call.name}\n"
            f"observation JSON:\n{json.dumps(payload, ensure_ascii=False, default=str)}"
        ),
    }
