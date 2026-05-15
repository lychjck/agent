import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from stock_assistant.agents.agent_llm import llm_structured_kwargs
from stock_assistant.core.llm import call_llm


class LlmToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LlmToolStep(BaseModel):
    type: Literal["research_plan", "tool_calls", "observation_reflection", "final_report"]
    tool_calls: list[LlmToolCall] = Field(default_factory=list)
    final_report: dict[str, Any] | None = None
    research_plan: dict[str, Any] | None = None
    observation_reflection: dict[str, Any] | None = None
    thinking_trace: dict[str, Any] = Field(default_factory=dict)
    missing_capabilities: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    raw_text: str = ""


def strip_json_markdown(text: str) -> str:
    clean = text.strip()
    if clean.startswith("<|channel>"):
        _, _, clean = clean.partition("\n")
        clean = clean.strip()
    clean = clean.replace("<channel|>", "").strip()
    if clean.startswith("```json"):
        clean = clean[7:].strip()
    elif clean.startswith("```"):
        clean = clean[3:].strip()
    if clean.endswith("```"):
        clean = clean[:-3].strip()
    return clean


def load_json_object(text: str) -> dict[str, Any]:
    clean = strip_json_markdown(text)
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(clean[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM tool step JSON 顶层必须是对象")
    return payload


def infer_step_type(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("type", "")).strip()
    if explicit in {"research_plan", "tool_calls", "observation_reflection", "final_report"}:
        return explicit
    if "observation_reflection" in payload or "satisfied_needs" in payload or "unsatisfied_needs" in payload:
        return "observation_reflection"
    if "research_plan" in payload or "information_needs" in payload or "missing_capabilities" in payload:
        return "research_plan"
    if "tool_call" in payload or "tool_calls" in payload:
        return "tool_calls"
    if "final_report" in payload or "report" in payload or "summary" in payload:
        return "final_report"
    raise ValueError("LLM tool step 缺少 type/tool_calls/final_report")


def normalize_tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "tool_calls" in payload:
        raw_calls = payload["tool_calls"]
    elif "tool_call" in payload:
        raw_calls = [payload["tool_call"]]
    else:
        raw_calls = []
    if not isinstance(raw_calls, list) or not raw_calls:
        raise ValueError("tool_calls 必须是非空数组")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_calls, start=1):
        if not isinstance(item, dict):
            raise ValueError("tool_call 必须是对象")
        name = item.get("name") or item.get("tool_name")
        if not name:
            raise ValueError("tool_call 缺少 name")
        arguments = item.get("arguments", {})
        if isinstance(arguments, str):
            arguments = load_json_object(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("tool_call.arguments 必须是对象")
        normalized.append({
            "id": str(item.get("id") or f"call_{index:03d}"),
            "name": str(name),
            "arguments": arguments,
        })
    return normalized


def normalize_final_report(payload: dict[str, Any]) -> dict[str, Any]:
    if "final_report" in payload:
        report = payload["final_report"]
    elif "report" in payload:
        report = payload["report"]
    else:
        report = payload
    if not isinstance(report, dict):
        raise ValueError("final_report 必须是对象")
    return report


def normalize_research_plan(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("research_plan", payload)
    if not isinstance(plan, dict):
        raise ValueError("research_plan 必须是对象")
    return plan


def normalize_observation_reflection(payload: dict[str, Any]) -> dict[str, Any]:
    reflection = payload.get("observation_reflection", payload)
    if not isinstance(reflection, dict):
        raise ValueError("observation_reflection 必须是对象")
    return reflection


def extract_reasoning_summary(payload: dict[str, Any]) -> str:
    for key in ("reasoning_summary", "decision_summary", "rationale", "reason"):
        value = payload.get(key)
        if value:
            return str(value).strip()
    return ""


def extract_thinking_trace(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("thinking_trace")
    if isinstance(value, dict):
        return value
    return {}


def extract_missing_capabilities(payload: dict[str, Any]) -> list[str]:
    value = payload.get("missing_capabilities")
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    thinking_trace = payload.get("thinking_trace")
    if isinstance(thinking_trace, dict) and isinstance(thinking_trace.get("missing_capabilities"), list):
        return [str(item) for item in thinking_trace["missing_capabilities"] if str(item).strip()]
    plan = payload.get("research_plan")
    if isinstance(plan, dict) and isinstance(plan.get("missing_capabilities"), list):
        return [str(item) for item in plan["missing_capabilities"] if str(item).strip()]
    return []


def parse_llm_tool_step(text: str) -> LlmToolStep:
    payload = load_json_object(text)
    step_type = infer_step_type(payload)
    reasoning_summary = extract_reasoning_summary(payload)
    thinking_trace = extract_thinking_trace(payload)
    missing_capabilities = extract_missing_capabilities(payload)
    try:
        if step_type == "research_plan":
            plan = normalize_research_plan(payload)
            return LlmToolStep(
                type="research_plan",
                research_plan=plan,
                thinking_trace=thinking_trace,
                missing_capabilities=missing_capabilities,
                reasoning_summary=reasoning_summary,
                raw_text=text,
            )
        if step_type == "observation_reflection":
            reflection = normalize_observation_reflection(payload)
            return LlmToolStep(
                type="observation_reflection",
                observation_reflection=reflection,
                thinking_trace=thinking_trace,
                missing_capabilities=missing_capabilities,
                reasoning_summary=reasoning_summary,
                raw_text=text,
            )
        if step_type == "tool_calls":
            return LlmToolStep(
                type="tool_calls",
                tool_calls=[LlmToolCall.model_validate(item) for item in normalize_tool_calls(payload)],
                thinking_trace=thinking_trace,
                missing_capabilities=missing_capabilities,
                reasoning_summary=reasoning_summary,
                raw_text=text,
            )
        return LlmToolStep(
            type="final_report",
            final_report=normalize_final_report(payload),
            thinking_trace=thinking_trace,
            missing_capabilities=missing_capabilities,
            reasoning_summary=reasoning_summary,
            raw_text=text,
        )
    except ValidationError as exc:
        raise ValueError(f"LLM tool step schema 校验失败: {exc}") from exc


def call_llm_tool_step(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    config: dict[str, Any],
    model_override: str | None = None,
) -> LlmToolStep:
    request_kwargs = llm_structured_kwargs(config)
    
    # 兼容原生工具调用：如果在配置中开启了原生工具支持，则将 tools 传给底层 SDK
    if tools and config.get("agent", {}).get("use_native_tools", False):
        request_kwargs["tools"] = tools
        # 如果模型支持强制工具调用，可以加上: request_kwargs["tool_choice"] = "auto"
        
    text = call_llm(
        messages,
        config,
        model_override=model_override,
        request_kwargs=request_kwargs,
    )
    try:
        return parse_llm_tool_step(text)
    except Exception as exc:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": (
                    "上一条输出不是合法的工具调用协议 JSON。"
                    f"解析错误: {exc}\n"
                    "请只输出一个合法 JSON 对象，不要 Markdown，不要解释。"
                    "如果这是第一轮规划，格式为 "
                    "{\"type\":\"research_plan\",\"reasoning_summary\":\"任务理解\","
                    "\"research_plan\":{\"information_needs\":[],\"available_tool_mapping\":[],\"missing_capabilities\":[]}}。"
                    "如果刚收到工具 observation，格式为 "
                    "{\"type\":\"observation_reflection\",\"reasoning_summary\":\"工具结果改变了什么判断\","
                    "\"observation_reflection\":{\"satisfied_needs\":[],\"unsatisfied_needs\":[],"
                    "\"observation_impact\":\"...\",\"next_action\":\"continue_tools\"}}。"
                    "如果需要继续查信息，格式为 "
                    "{\"type\":\"tool_calls\",\"reasoning_summary\":\"为什么需要调用这些工具\","
                    "\"tool_calls\":[{\"id\":\"call_fix_001\",\"name\":\"get_current_holdings\",\"arguments\":{}}]}。"
                    "如果信息足够，格式为 {\"type\":\"final_report\","
                    "\"reasoning_summary\":\"为什么可以生成最终报告\",\"report\":{...}}。"
                ),
            },
        ]
        fixed = call_llm(
            repair_messages,
            config,
            model_override=model_override,
            request_kwargs=request_kwargs,
        )
        return parse_llm_tool_step(fixed)
