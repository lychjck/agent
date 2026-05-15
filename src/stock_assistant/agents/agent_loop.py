import datetime as dt
import json
import time
from typing import Any, AsyncIterator

from stock_assistant.agents.agent import save_ai_report
from stock_assistant.agents.agent_executor import execute_tool_call, tool_observation_message
from stock_assistant.agents.agent_llm import agent_report_schema_hint, parse_agent_report
from stock_assistant.agents.agent_tools import build_agent_tool_registry, tool_schemas
from stock_assistant.agents.agent_trace import AgentTraceWriter
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.llm import llm_enabled
from stock_assistant.core.llm_tools import LlmToolCall, call_llm_tool_step
from stock_assistant.core.memory import agent_snapshots_have_same_facts, save_agent_snapshot
from stock_assistant.core.utils import config_bool, log


def agent_run_id() -> str:
    return f"agent-{dt.datetime.now():%Y%m%d-%H%M%S-%f}"


def tool_agent_event(step: str, status: str = "", **extra: Any) -> dict[str, Any]:
    event = {"step": step}
    if status:
        event["status"] = status
    event.update(extra)
    return event


def compact_for_log(value: Any, max_chars: int = 500) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"...[truncated {len(text)} chars]"


def agent_log(run_id: str, message: str, *, turn: int | None = None, level: str = "INFO") -> None:
    turn_part = f" turn={turn}" if turn is not None else ""
    log(f"[tool-agent run={run_id}{turn_part}] {message}", level=level, name="tool_agent")


def build_initial_agent_messages(goal: str, tools: list[dict[str, Any]], use_native_tools: bool = False) -> list[dict[str, str]]:
    tool_text = ""
    if not use_native_tools:
        tool_json = json.dumps(tools, ensure_ascii=False, indent=2)
        tool_text = f"可用工具如下。你只能调用这些工具，arguments 必须符合 parameters。\n{tool_json}\n\n"
        
    report_schema = json.dumps(agent_report_schema_hint(), ensure_ascii=False, indent=2)
    system = (
        "你是一个受控的中文持仓分析工具调用 Agent。"
        "你不能直接读取文件、Cookie、API key 或原始账户响应。"
        "你必须先从任务本身推导信息需求，再把信息需求映射到可用工具。"
        "如果任务可能受益于用户安装的 skill、专门流程或领域方法，必须把 skill 发现纳入信息需求。"
        "不要因为当前没有工具就假装信息足够；缺工具时必须显式记录 missing_capabilities。"
        "需要信息时，只能从给定工具列表中选择工具，并输出合法 JSON。"
        "信息不足时继续调用工具；信息足够时输出 final_report。"
        "每次工具 observation 之后，下一轮必须先输出 observation_reflection，不能直接输出 final_report。"
        "每次输出都要包含 reasoning_summary 和 thinking_trace，用中文充分说明可审计的决策依据。"
        "不要输出隐藏推理链；但 thinking_trace 不能过短，必须覆盖事实、缺口、影响、下一步。"
        "不要输出 Markdown 包裹。"
    )
    user = (
        f"用户目标：{goal}\n\n"
        f"{tool_text}"
        "第一轮必须输出 research_plan，不能调用工具，不能输出 final_report。"
        "research_plan 必须先从任务出发列出 information_needs，再列 available_tool_mapping 和 missing_capabilities。\n"
        "ETF/基金分析的信息需求至少考虑：当前组合权重、标的类型、跟踪指数、底层持仓/前十大持仓、行业/区域/风格暴露、"
        "标的自身 K 线、底层核心资产趋势、同类替代品、历史变化、限制条件。"
        "如果可用工具里有 list_skills/read_skill，且任务可能匹配用户安装的 skill，先列出 skill 发现需求；"
        "读取 skill 后必须按其 SKILL.md 的约束工作，并在 observation_reflection 中说明采用了哪个 skill。"
        "如果可用工具里有 web_search/web_read，搜索任务应优先用 web_search 获取结构化结果，再用 web_read 打开具体来源；"
        "只有需要直接访问某个 URL 时才使用底层 web_fetch。"
        "当前工具无法获取的信息必须写入 missing_capabilities，例如 ETF 底层持仓、跟踪指数、指数成分、成分股 K 线等。\n\n"
        "每次输出必须包含 reasoning_summary 和 thinking_trace。thinking_trace 用对象表达："
        "task_understanding、known_facts、information_needs、available_tool_mapping、missing_capabilities、decision_basis、next_step。\n\n"
        "第一轮研究计划输出格式：\n"
        "{\"type\":\"research_plan\",\"reasoning_summary\":\"我先从 ETF 分析任务推导需要验证的信息，而不是直接按工具列表行动。\","
        "\"thinking_trace\":{\"task_understanding\":\"...\",\"information_needs\":[\"...\"],"
        "\"available_tool_mapping\":[{\"need\":\"当前组合权重\",\"tool\":\"get_current_holdings\"}],"
        "\"missing_capabilities\":[\"ETF 底层持仓工具\"],\"decision_basis\":\"...\",\"next_step\":\"...\"},"
        "\"research_plan\":{\"information_needs\":[\"...\"],\"available_tool_mapping\":[{\"need\":\"...\",\"tool\":\"...\"}],"
        "\"missing_capabilities\":[\"...\"],\"execution_strategy\":\"先获取已有工具可验证的信息，同时保留缺失能力限制。\"}}\n\n"
        "工具调用输出格式：\n"
        "{\"type\":\"tool_calls\",\"reasoning_summary\":\"我还缺少当前持仓明细，所以先读取脱敏持仓。\","
        "\"thinking_trace\":{\"known_facts\":[\"...\"],\"information_needs\":[\"...\"],"
        "\"available_tool_mapping\":[{\"need\":\"...\",\"tool\":\"get_current_holdings\"}],"
        "\"missing_capabilities\":[\"...\"],\"decision_basis\":\"...\",\"next_step\":\"...\"},"
        "\"tool_calls\":[{\"id\":\"call_001\",\"name\":\"get_current_holdings\",\"arguments\":{}}]}\n\n"
        "工具结果反思输出格式：\n"
        "{\"type\":\"observation_reflection\",\"reasoning_summary\":\"我根据刚返回的工具结果更新了研究状态。\","
        "\"thinking_trace\":{\"known_facts\":[\"...\"],\"satisfied_needs\":[\"...\"],"
        "\"unsatisfied_needs\":[\"...\"],\"missing_capabilities\":[\"...\"],"
        "\"observation_impact\":\"...\",\"decision_basis\":\"...\",\"next_step\":\"...\"},"
        "\"observation_reflection\":{\"satisfied_needs\":[\"...\"],\"unsatisfied_needs\":[\"...\"],"
        "\"observation_impact\":\"工具结果改变/确认了什么判断\","
        "\"coverage_notes\":\"哪些标的已经覆盖，哪些还没覆盖\","
        "\"next_action\":\"continue_tools 或 final_report\","
        "\"required_tool_calls\":[{\"tool\":\"get_holding_technical\",\"reason\":\"...\"}]}}\n\n"
        "最终报告输出格式：\n"
        "{\"type\":\"final_report\",\"reasoning_summary\":\"已经读取了持仓、画像和必要技术指标，可以生成报告。\","
        "\"thinking_trace\":{\"known_facts\":[\"...\"],\"missing_capabilities\":[\"...\"],"
        "\"decision_basis\":\"哪些证据足够，哪些只能作为限制说明\"},"
        "\"report\":{...}}\n\n"
        f"最终 report 必须符合这个 schema：\n{report_schema}\n\n"
        "最终报告前必须至少有一次 observation_reflection，并且最近一次 observation_reflection 的 next_action 必须是 final_report。"
        "如果目标要求每个 ETF 的建议，必须在 reflection.coverage_notes 中说明覆盖范围；没有足够数据的标的要列为未覆盖或限制。"
        "现在只输出第一轮 research_plan。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def llm_decision_payload(step: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": step.type,
        "reasoning_summary": step.reasoning_summary,
        "thinking_trace": step.thinking_trace,
        "missing_capabilities": step.missing_capabilities,
    }
    if step.type == "research_plan":
        payload["research_plan"] = step.research_plan or {}
    elif step.type == "observation_reflection":
        payload["observation_reflection"] = step.observation_reflection or {}
    elif step.type == "tool_calls":
        payload["tool_calls"] = [call.model_dump() for call in step.tool_calls]
    else:
        payload["final_report"] = step.final_report or {}
    return payload


def build_act_prompt() -> str:
    return (
        "已记录 research_plan。现在进入执行阶段："
        "只能对 available_tool_mapping 中当前工具能满足的信息需求调用工具。"
        "每次调用工具前，在 thinking_trace.decision_basis 中说明为什么这个工具能推进任务。"
        "如果已有证据不足，不要输出 final_report；如果缺少 ETF 底层持仓/指数成分等能力，"
        "继续在 missing_capabilities 中保留，不要臆测。"
    )


def build_reflection_prompt() -> str:
    return (
        "你刚收到了一个或多个工具 observation。下一步必须输出 observation_reflection，不能调用工具，也不能输出 final_report。"
        "请审查：哪些 information_needs 已满足、哪些未满足、工具结果如何改变判断、"
        "是否覆盖了用户要求的每个 ETF 建议、下一步应该继续调用哪些工具或是否可以最终报告。"
        "如果还缺 ETF 底层持仓/指数成分等能力，要继续保留在 missing_capabilities，并说明这对建议强度的影响。"
        "observation_reflection.next_action 只能是 continue_tools 或 final_report。"
    )


def build_after_reflection_prompt(reflection: dict[str, Any]) -> str:
    next_action = str(reflection.get("next_action", "")).strip()
    if next_action == "final_report":
        return (
            "已记录 observation_reflection，且 next_action=final_report。"
            "现在可以输出 final_report，但必须在 limitations 中保留缺失能力造成的限制，"
            "并区分已验证结论和数据不足的标的。"
        )
    return (
        "已记录 observation_reflection。现在请根据 required_tool_calls 或 unsatisfied_needs 继续调用工具。"
        "如果当前工具无法满足某个需求，不要臆测；保留 missing_capabilities，并选择还能推进任务的可用工具。"
    )


def reflection_next_action(reflection: dict[str, Any] | None) -> str:
    if not isinstance(reflection, dict):
        return ""
    return str(reflection.get("next_action", "")).strip()


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def split_oversized_tool_call(call: LlmToolCall) -> list[LlmToolCall]:
    if call.name not in {"get_holding_technical", "get_classification"}:
        return [call]
    codes = call.arguments.get("codes")
    if not isinstance(codes, list):
        return [call]
    limit = 20 if call.name == "get_holding_technical" else 50
    if len(codes) <= limit:
        return [call]
    split_calls: list[LlmToolCall] = []
    for index, code_chunk in enumerate(chunked([str(code) for code in codes], limit), start=1):
        arguments = dict(call.arguments)
        arguments["codes"] = code_chunk
        split_calls.append(
            LlmToolCall(
                id=f"{call.id or call.name}_part_{index:02d}",
                name=call.name,
                arguments=arguments,
            )
        )
    return split_calls


def split_oversized_tool_calls(calls: list[LlmToolCall]) -> list[LlmToolCall]:
    output: list[LlmToolCall] = []
    for call in calls:
        output.extend(split_oversized_tool_call(call))
    return output


def goal_requires_full_technical_coverage(goal: str) -> bool:
    normalized = goal.strip()
    return any(token in normalized for token in ("每个", "全部", "所有", "逐个"))


def missing_technical_codes(workspace: AgentWorkspace, goal: str) -> list[str]:
    if not goal_requires_full_technical_coverage(goal):
        return []
    existing = {
        str((item.get("holding") or {}).get("code", ""))
        for item in workspace.technical_results
        if isinstance(item.get("holding"), dict)
    }
    return [
        holding.code
        for holding in workspace.ensure_holdings()
        if holding.code and holding.code not in existing
    ]


def build_coverage_prompt(missing_codes: list[str]) -> str:
    return (
        "最终报告暂缓：用户目标要求逐个覆盖当前持仓，但仍有标的缺少 technical observation。"
        f"后端已补充请求缺失标的技术指标，缺失数量={len(missing_codes)}。"
        "收到这些 observation 后必须重新做 observation_reflection；只有覆盖缺口清零，"
        "或 observation 明确说明某个标的不可分析，才可以 final_report。"
    )


async def run_tool_agent_events(
    config: dict[str, Any],
    *,
    goal: str,
    cached_results: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
    save_snapshot: bool = True,
    save_report: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    if not config_bool(config.get("agent", {}).get("tool_agent_enabled", False)):
        log("[tool-agent] disabled by agent.tool_agent_enabled=false", level="WARN", name="tool_agent")
        yield tool_agent_event("error", "LLM 工具调用 Agent 未启用", error="agent.tool_agent_enabled=false")
        return
    if not llm_enabled(config):
        log("[tool-agent] disabled because llm.enabled=false", level="WARN", name="tool_agent")
        yield tool_agent_event("error", "LLM 未启用，无法运行工具调用 Agent", error="llm.enabled=false")
        return

    run_id = agent_run_id()
    model = model_override or config.get("llm", {}).get("model", "unknown")
    trace = AgentTraceWriter(config, run_id)
    workspace = AgentWorkspace(config, cached_results=cached_results)
    registry = build_agent_tool_registry(config)
    schemas = tool_schemas(registry)
    use_native_tools = config_bool(config.get("agent", {}).get("use_native_tools", False))
    messages = build_initial_agent_messages(goal, schemas, use_native_tools=use_native_tools)
    max_turns = int(config.get("agent", {}).get("max_tool_turns", 12) or 12)
    max_calls = int(config.get("agent", {}).get("max_tool_calls", 16) or 16)
    tool_call_count = 0
    reflection_required = False
    reflection_seen = False
    last_reflection: dict[str, Any] | None = None

    trace.write("agent_start", {"goal": goal, "model": model, "tools": list(registry)})
    trace_status = str(trace.path) if trace.enabled else "disabled"
    agent_log(
        run_id,
        (
            f"start model={model} tools={len(registry)} max_turns={max_turns} "
            f"max_calls={max_calls} cached_results={len(cached_results or [])} trace={trace_status} "
            f"goal={goal[:160]}"
        ),
    )
    yield tool_agent_event("agent_start", "开始 AI 工具调用分析", run_id=run_id)

    for turn in range(1, max_turns + 1):
        trace.write("llm_request", {"turn": turn, "message_count": len(messages)})
        agent_log(run_id, f"llm_request messages={len(messages)}", turn=turn)
        yield tool_agent_event("llm_turn", "AI 正在决定下一步", run_id=run_id, turn=turn)

        try:
            started = time.monotonic()
            step = call_llm_tool_step(messages, schemas, config, model_override=model_override)
            elapsed = time.monotonic() - started
        except Exception as exc:  # noqa: BLE001
            trace.write("error", {"turn": turn, "error": str(exc)})
            agent_log(run_id, f"llm_response_parse_failed error={exc}", turn=turn, level="ERROR")
            yield tool_agent_event("error", "LLM 工具调用输出无法解析", run_id=run_id, error=str(exc))
            return

        trace.write("llm_response", {"turn": turn, "type": step.type, "raw_text": step.raw_text})
        messages.append({"role": "assistant", "content": step.raw_text})
        decision_payload = llm_decision_payload(step)
        if turn == 1 and step.type != "research_plan":
            message = f"第一轮必须输出 research_plan，实际输出 {step.type}"
            trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
            agent_log(run_id, message, turn=turn, level="ERROR")
            yield tool_agent_event(
                "error",
                "LLM 未先生成研究计划",
                run_id=run_id,
                turn=turn,
                error=message,
                raw_text=step.raw_text,
            )
            return
        if reflection_required and step.type != "observation_reflection":
            message = f"工具 observation 后必须先输出 observation_reflection，实际输出 {step.type}"
            trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
            agent_log(run_id, message, turn=turn, level="ERROR")
            yield tool_agent_event(
                "error",
                "LLM 未先反思工具结果",
                run_id=run_id,
                turn=turn,
                error=message,
                raw_text=step.raw_text,
            )
            return
        if step.type == "research_plan":
            agent_log(
                run_id,
                (
                    f"research_plan needs={len((step.research_plan or {}).get('information_needs', []) or [])} "
                    f"missing={len(step.missing_capabilities)} elapsed={elapsed:.2f}s"
                ),
                turn=turn,
            )
            yield tool_agent_event(
                "research_plan",
                "AI 已生成研究计划",
                run_id=run_id,
                turn=turn,
                decision_type=step.type,
                elapsed_seconds=round(elapsed, 2),
                reasoning_summary=step.reasoning_summary,
                thinking_trace=step.thinking_trace,
                missing_capabilities=step.missing_capabilities,
                raw_text=step.raw_text,
                parsed=decision_payload,
                research_plan=step.research_plan or {},
            )
            messages.append({"role": "user", "content": build_act_prompt()})
            continue

        if step.type == "observation_reflection":
            reflection_required = False
            reflection_seen = True
            last_reflection = step.observation_reflection or {}
            next_action = reflection_next_action(last_reflection)
            agent_log(
                run_id,
                (
                    f"observation_reflection satisfied={len(last_reflection.get('satisfied_needs', []) or [])} "
                    f"unsatisfied={len(last_reflection.get('unsatisfied_needs', []) or [])} "
                    f"next_action={next_action or '-'} elapsed={elapsed:.2f}s"
                ),
                turn=turn,
            )
            yield tool_agent_event(
                "observation_reflection",
                "AI 已反思工具结果",
                run_id=run_id,
                turn=turn,
                decision_type=step.type,
                elapsed_seconds=round(elapsed, 2),
                reasoning_summary=step.reasoning_summary,
                thinking_trace=step.thinking_trace,
                missing_capabilities=step.missing_capabilities,
                raw_text=step.raw_text,
                parsed=decision_payload,
                observation_reflection=last_reflection,
            )
            missing_codes = missing_technical_codes(workspace, goal)
            if missing_codes:
                agent_log(
                    run_id,
                    f"coverage_gate_after_reflection missing_technical={len(missing_codes)}",
                    turn=turn,
                    level="WARN",
                )
                yield tool_agent_event(
                    "coverage_gate",
                    f"仍有 {len(missing_codes)} 个标的缺少技术指标，后端自动补查",
                    run_id=run_id,
                    turn=turn,
                    missing_codes=missing_codes,
                )
                for index, code_chunk in enumerate(chunked(missing_codes, 20), start=1):
                    tool_call_count += 1
                    if tool_call_count > max_calls:
                        message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
                        trace.write("error", {"turn": turn, "error": message})
                        agent_log(run_id, message, turn=turn, level="ERROR")
                        yield tool_agent_event("error", message, run_id=run_id, error=message)
                        return
                    call = LlmToolCall(
                        id=f"auto_coverage_{turn}_{index:02d}",
                        name="get_holding_technical",
                        arguments={"codes": code_chunk},
                    )
                    trace.write("tool_call", {"turn": turn, "call": call.model_dump(), "reason": "coverage_gate"})
                    yield tool_agent_event(
                        "tool_call",
                        f"补查缺失技术指标：{len(code_chunk)} 个标的",
                        run_id=run_id,
                        turn=turn,
                        tool=call.name,
                        arguments=call.arguments,
                        auto=True,
                    )
                    started = time.monotonic()
                    observation = execute_tool_call(call, registry, workspace)
                    elapsed = time.monotonic() - started
                    trace.write("tool_observation", {"turn": turn, "observation": observation.model_dump()})
                    agent_log(
                        run_id,
                        (
                            f"coverage_observation ok={observation.ok} elapsed={elapsed:.2f}s "
                            f"summary={observation.summary or observation.message}"
                        ),
                        turn=turn,
                        level="INFO" if observation.ok else "WARN",
                    )
                    yield tool_agent_event(
                        "tool_observation",
                        observation.summary or observation.message,
                        run_id=run_id,
                        turn=turn,
                        tool=call.name,
                        ok=observation.ok,
                        summary=observation.summary,
                        error_type=observation.error_type,
                        message=observation.message,
                        observation=observation.model_dump(),
                        auto=True,
                    )
                    messages.append(tool_observation_message(call, observation))
                reflection_required = True
                messages.append({"role": "user", "content": build_coverage_prompt(missing_codes)})
                messages.append({"role": "user", "content": build_reflection_prompt()})
                continue
            messages.append({"role": "user", "content": build_after_reflection_prompt(last_reflection)})
            continue

        if step.type == "tool_calls":
            step.tool_calls = split_oversized_tool_calls(step.tool_calls)
            tool_names = [call.name for call in step.tool_calls]
            agent_log(
                run_id,
                f"llm_decision type=tool_calls count={len(tool_names)} tools={tool_names} elapsed={elapsed:.2f}s",
                turn=turn,
            )
            yield tool_agent_event(
                "llm_decision",
                f"AI 决定调用 {len(tool_names)} 个工具",
                run_id=run_id,
                turn=turn,
                decision_type=step.type,
                elapsed_seconds=round(elapsed, 2),
                reasoning_summary=step.reasoning_summary,
                thinking_trace=step.thinking_trace,
                missing_capabilities=step.missing_capabilities,
                raw_text=step.raw_text,
                parsed=decision_payload,
            )
        else:
            agent_log(run_id, f"llm_decision type=final_report elapsed={elapsed:.2f}s", turn=turn)
            yield tool_agent_event(
                "llm_decision",
                "AI 决定生成最终报告",
                run_id=run_id,
                turn=turn,
                decision_type=step.type,
                elapsed_seconds=round(elapsed, 2),
                reasoning_summary=step.reasoning_summary,
                thinking_trace=step.thinking_trace,
                missing_capabilities=step.missing_capabilities,
                raw_text=step.raw_text,
                parsed=decision_payload,
            )

        if step.type == "final_report":
            if not reflection_seen:
                message = "final_report 前必须至少有一次 observation_reflection"
                trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
                agent_log(run_id, message, turn=turn, level="ERROR")
                yield tool_agent_event("error", message, run_id=run_id, turn=turn, error=message)
                return
            if reflection_next_action(last_reflection) != "final_report":
                message = "最近一次 observation_reflection.next_action 不是 final_report，不能生成最终报告"
                trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
                agent_log(run_id, message, turn=turn, level="ERROR")
                yield tool_agent_event("error", message, run_id=run_id, turn=turn, error=message)
                return
            missing_codes = missing_technical_codes(workspace, goal)
            if missing_codes:
                agent_log(
                    run_id,
                    f"final_report deferred missing_technical={len(missing_codes)}",
                    turn=turn,
                    level="WARN",
                )
                yield tool_agent_event(
                    "coverage_gate",
                    f"最终报告暂缓：仍有 {len(missing_codes)} 个标的缺少技术指标",
                    run_id=run_id,
                    turn=turn,
                    missing_codes=missing_codes,
                )
                for index, code_chunk in enumerate(chunked(missing_codes, 20), start=1):
                    tool_call_count += 1
                    if tool_call_count > max_calls:
                        message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
                        trace.write("error", {"turn": turn, "error": message})
                        agent_log(run_id, message, turn=turn, level="ERROR")
                        yield tool_agent_event("error", message, run_id=run_id, error=message)
                        return
                    call = LlmToolCall(
                        id=f"auto_coverage_{turn}_{index:02d}",
                        name="get_holding_technical",
                        arguments={"codes": code_chunk},
                    )
                    trace.write("tool_call", {"turn": turn, "call": call.model_dump(), "reason": "coverage_gate"})
                    yield tool_agent_event(
                        "tool_call",
                        f"补查缺失技术指标：{len(code_chunk)} 个标的",
                        run_id=run_id,
                        turn=turn,
                        tool=call.name,
                        arguments=call.arguments,
                        auto=True,
                    )
                    started = time.monotonic()
                    observation = execute_tool_call(call, registry, workspace)
                    elapsed = time.monotonic() - started
                    trace.write("tool_observation", {"turn": turn, "observation": observation.model_dump()})
                    agent_log(
                        run_id,
                        (
                            f"coverage_observation ok={observation.ok} elapsed={elapsed:.2f}s "
                            f"summary={observation.summary or observation.message}"
                        ),
                        turn=turn,
                        level="INFO" if observation.ok else "WARN",
                    )
                    yield tool_agent_event(
                        "tool_observation",
                        observation.summary or observation.message,
                        run_id=run_id,
                        turn=turn,
                        tool=call.name,
                        ok=observation.ok,
                        summary=observation.summary,
                        error_type=observation.error_type,
                        message=observation.message,
                        observation=observation.model_dump(),
                        auto=True,
                    )
                    messages.append(tool_observation_message(call, observation))
                reflection_required = True
                messages.append({"role": "user", "content": build_coverage_prompt(missing_codes)})
                messages.append({"role": "user", "content": build_reflection_prompt()})
                continue
            llm_context = workspace.build_llm_context()
            report = parse_agent_report(
                json.dumps(step.final_report or {}, ensure_ascii=False),
                [],
                llm_context.get("evidence_index", {}),
                config,
                observations=list(llm_context.get("observations", [])),
                holdings=list(llm_context.get("holdings", [])),
            )
            snapshot = workspace.build_snapshot(report, model)
            trace.write("final_report", {"turn": turn, "summary": report.get("summary", {})})
            summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
            agent_log(
                run_id,
                (
                    f"final_report status={summary.get('status', '')} "
                    f"health={summary.get('health_score', '')} brief={str(summary.get('brief', ''))[:180]}"
                ),
                turn=turn,
            )
            yield tool_agent_event("final_report", "AI 已生成最终报告", run_id=run_id, turn=turn)

            if save_snapshot and config_bool(config.get("agent", {}).get("save_snapshots", True)):
                if agent_snapshots_have_same_facts(workspace.previous_snapshot(), snapshot):
                    agent_log(run_id, "snapshot skipped reason=same_facts")
                    yield tool_agent_event("save_snapshot", "事实数据未变化，跳过重复保存", run_id=run_id)
                else:
                    save_agent_snapshot(snapshot, config)
                    agent_log(run_id, "snapshot saved")
                    yield tool_agent_event("save_snapshot", "已保存 Agent 快照", run_id=run_id)
            if save_report:
                save_ai_report(workspace.technical_results, report, model, config)
                agent_log(run_id, f"ai_report saved technical_results={len(workspace.technical_results)}")

            trace.write("done", {"turn": turn})
            agent_log(run_id, "done")
            yield tool_agent_event("done", "Agent 分析完成", run_id=run_id, snapshot=snapshot)
            return

        for call in step.tool_calls:
            tool_call_count += 1
            if tool_call_count > max_calls:
                message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
                trace.write("error", {"turn": turn, "error": message})
                agent_log(run_id, message, turn=turn, level="ERROR")
                yield tool_agent_event("error", message, run_id=run_id, error=message)
                return

            trace.write("tool_call", {"turn": turn, "call": call.model_dump()})
            agent_log(
                run_id,
                f"tool_call index={tool_call_count}/{max_calls} name={call.name} args={compact_for_log(call.arguments)}",
                turn=turn,
            )
            yield tool_agent_event(
                "tool_call",
                f"调用工具：{call.name}",
                run_id=run_id,
                turn=turn,
                tool=call.name,
                arguments=call.arguments,
            )
            started = time.monotonic()
            observation = execute_tool_call(call, registry, workspace)
            elapsed = time.monotonic() - started
            trace.write("tool_observation", {"turn": turn, "observation": observation.model_dump()})
            observation_log_level = "INFO" if observation.ok else "WARN"
            agent_log(
                run_id,
                (
                    f"tool_observation name={call.name} ok={observation.ok} elapsed={elapsed:.2f}s "
                    f"summary={observation.summary or observation.message} "
                    f"error_type={observation.error_type or '-'}"
                ),
                turn=turn,
                level=observation_log_level,
            )
            yield tool_agent_event(
                "tool_observation",
                observation.summary or observation.message,
                run_id=run_id,
                turn=turn,
                tool=call.name,
                ok=observation.ok,
                summary=observation.summary,
                error_type=observation.error_type,
                message=observation.message,
                observation=observation.model_dump(),
            )
            messages.append(tool_observation_message(call, observation))

        if step.type == "tool_calls":
            reflection_required = True
            messages.append({"role": "user", "content": build_reflection_prompt()})

    message = f"达到 max_tool_turns={max_turns}，Agent 未完成"
    trace.write("error", {"error": message})
    agent_log(run_id, message, level="ERROR")
    yield tool_agent_event("error", message, run_id=run_id, error=message)
