import time
from typing import Any, AsyncIterator

from stock_assistant.agents.agent_coverage import (
    build_external_research_gate_prompt,
    build_holding_analysis_gate_prompt,
    external_research_gap,
    final_report_missing_holding_analysis,
    missing_technical_codes,
)
from stock_assistant.agents.agent_loop_events import agent_log, tool_agent_event
from stock_assistant.agents.agent_loop_handlers import (
    persist_final_report,
    run_missing_technical_gate,
    run_tool_calls,
)
from stock_assistant.agents.agent_loop_runtime import build_agent_loop_runtime
from stock_assistant.agents.agent_protocol import (
    build_act_prompt,
    build_after_reflection_prompt,
    build_initial_agent_messages,
    llm_decision_payload,
    reflection_next_action,
)
from stock_assistant.agents.agent_report_merge import merge_final_report_patch, normalize_report_payload
from stock_assistant.agents.agent_tool_batch import split_oversized_tool_calls
from stock_assistant.core.llm import llm_enabled
from stock_assistant.core.llm_tools import call_llm_tool_step
from stock_assistant.core.utils import config_bool, log


async def run_tool_agent_events(
    config: dict[str, Any],
    *,
    goal: str,
    cached_results: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
    save_snapshot: bool = True,
    save_report: bool = True,
    resume_state: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    if not config_bool(config.get("agent", {}).get("tool_agent_enabled", False)):
        log("[tool-agent] disabled by agent.tool_agent_enabled=false", level="WARN", name="tool_agent")
        yield tool_agent_event("error", "LLM 工具调用 Agent 未启用", error="agent.tool_agent_enabled=false")
        return
    if not llm_enabled(config):
        log("[tool-agent] disabled because llm.enabled=false", level="WARN", name="tool_agent")
        yield tool_agent_event("error", "LLM 未启用，无法运行工具调用 Agent", error="llm.enabled=false")
        return

    runtime = build_agent_loop_runtime(
        config,
        goal=goal,
        cached_results=cached_results,
        model_override=model_override,
        save_snapshot=save_snapshot,
        save_report=save_report,
        resume_state=resume_state,
    )
    run_id = runtime.run_id
    model = runtime.model
    trace = runtime.trace
    workspace = runtime.workspace
    registry = runtime.registry
    schemas = runtime.schemas
    state = runtime.state
    max_turns = runtime.max_turns
    start_turn = runtime.start_turn
    max_calls = runtime.max_calls

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
    yield tool_agent_event("agent_start", "继续 AI 工具调用分析" if resume_state else "开始 AI 工具调用分析", run_id=run_id)

    for turn in range(start_turn, max_turns + 1):
        trace.write("llm_request", {"turn": turn, "message_count": len(state.messages)})
        agent_log(run_id, f"llm_request messages={len(state.messages)}", turn=turn)
        yield tool_agent_event("llm_turn", "AI 正在决定下一步", run_id=run_id, turn=turn)

        try:
            started = time.monotonic()
            step = call_llm_tool_step(state.messages, schemas, config, model_override=model_override)
            elapsed = time.monotonic() - started
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
            if state.reflection_required and not detail:
                detail = "工具 observation 后必须先输出 observation_reflection"
            trace.write("error", {"turn": turn, "error": detail})
            agent_log(run_id, f"llm_response_parse_failed paused error={detail}", turn=turn, level="ERROR")
            state.messages.append({
                "role": "user",
                "content": (
                    "上一轮输出无法解析，运行已暂停并保留上下文。继续时请只输出一个合法 JSON 对象，"
                    "不要 Markdown，不要 channel 标记。根据当前证据继续：如果还缺信息输出 tool_calls，"
                    "如果刚收到工具 observation 输出 observation_reflection，如果信息足够输出 final_report。"
                ),
            })
            yield tool_agent_event(
                "paused",
                "LLM 输出无法解析，已暂停并保存可继续状态",
                run_id=run_id,
                turn=turn,
                error=detail,
                checkpoint=state.checkpoint(workspace, turn + 1),
            )
            return

        trace.write("llm_response", {"turn": turn, "type": step.type, "raw_text": step.raw_text})
        state.messages.append({"role": "assistant", "content": step.raw_text})
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
        if state.reflection_required and step.type != "observation_reflection":
            message = f"工具 observation 后必须先输出 observation_reflection，实际输出 {step.type}"
            trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
            agent_log(run_id, message, turn=turn, level="WARN")
            yield tool_agent_event(
                "protocol_repair",
                "LLM 未先反思工具结果，已要求重新输出 observation_reflection",
                run_id=run_id,
                turn=turn,
                warning=message,
                raw_text=step.raw_text,
            )
            state.messages.append({
                "role": "user",
                "content": (
                    "协议纠偏：你刚收到工具 observation 后，必须先输出 observation_reflection，"
                    f"但你输出了 {step.type}。请忽略上一条格式错误的输出，"
                    "现在只输出一个合法 JSON 对象，type 必须是 observation_reflection；"
                    "不能输出 research_plan、tool_calls 或 final_report。"
                    "observation_reflection.next_action 只能是 continue_tools 或 final_report。"
                ),
            })
            continue
        if turn > 1 and step.type == "research_plan":
            message = "research_plan 只能在第一轮输出，中途重新规划会丢失执行上下文"
            trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
            agent_log(run_id, message, turn=turn, level="WARN")
            yield tool_agent_event(
                "protocol_repair",
                "LLM 中途重新输出研究计划，已要求回到当前执行状态",
                run_id=run_id,
                turn=turn,
                warning=message,
                raw_text=step.raw_text,
            )
            state.messages.append({
                "role": "user",
                "content": (
                    "协议纠偏：research_plan 只能在第一轮输出，当前已经在执行阶段。"
                    "请忽略上一条 research_plan，基于已有 messages 和 observations 继续。"
                    "如果还缺信息，输出 tool_calls；如果刚收到工具 observation，输出 observation_reflection；"
                    "如果证据足够，输出 final_report。只能输出一个合法 JSON 对象。"
                ),
            })
            continue
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
            state.messages.append({"role": "user", "content": build_act_prompt()})
            continue

        if step.type == "observation_reflection":
            last_reflection = state.accept_reflection(step.observation_reflection)
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
                events, completed = run_missing_technical_gate(
                    run_id=run_id,
                    turn=turn,
                    trace=trace,
                    registry=registry,
                    workspace=workspace,
                    state=state,
                    max_calls=max_calls,
                    missing_codes=missing_codes,
                    gate_status=f"仍有 {len(missing_codes)} 个标的缺少技术指标，后端自动补查",
                )
                for event in events:
                    yield event
                if not completed:
                    return
                continue
            external_gap = external_research_gap(
                workspace,
                goal,
                registry,
                state.web_search_queries,
                state.web_read_count,
            )
            if next_action == "final_report" and external_gap:
                agent_log(
                    run_id,
                    (
                        "external_research_gate_after_reflection "
                        f"missing={len(external_gap.get('missing_holding_research') or [])} "
                        f"web_read_count={state.web_read_count} web_search_count={len(state.web_search_queries)}"
                    ),
                    turn=turn,
                    level="WARN",
                )
                trace.write("coverage_gate", {"turn": turn, "type": "external_research", "gap": external_gap})
                yield tool_agent_event(
                    "coverage_gate",
                    "外部研究覆盖不足，暂缓最终报告",
                    run_id=run_id,
                    turn=turn,
                    missing_holding_research=external_gap.get("missing_holding_research", []),
                    web_read_count=state.web_read_count,
                    searched_queries=state.web_search_queries,
                )
                state.messages.append({"role": "user", "content": build_external_research_gate_prompt(external_gap)})
                continue
            state.messages.append({"role": "user", "content": build_after_reflection_prompt(last_reflection)})
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
            if state.pending_final_report:
                step.final_report = merge_final_report_patch(state.pending_final_report, step.final_report)
                state.pending_final_report = None
            if not state.reflection_seen:
                message = "final_report 前必须至少有一次 observation_reflection"
                trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
                agent_log(run_id, message, turn=turn, level="ERROR")
                yield tool_agent_event("error", message, run_id=run_id, turn=turn, error=message)
                return
            if reflection_next_action(state.last_reflection) != "final_report":
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
                events, completed = run_missing_technical_gate(
                    run_id=run_id,
                    turn=turn,
                    trace=trace,
                    registry=registry,
                    workspace=workspace,
                    state=state,
                    max_calls=max_calls,
                    missing_codes=missing_codes,
                    gate_status=f"最终报告暂缓：仍有 {len(missing_codes)} 个标的缺少技术指标",
                )
                for event in events:
                    yield event
                if not completed:
                    return
                continue
            external_gap = external_research_gap(
                workspace,
                goal,
                registry,
                state.web_search_queries,
                state.web_read_count,
            )
            if external_gap:
                agent_log(
                    run_id,
                    (
                        "final_report deferred external_research "
                        f"missing={len(external_gap.get('missing_holding_research') or [])} "
                        f"web_read_count={state.web_read_count} web_search_count={len(state.web_search_queries)}"
                    ),
                    turn=turn,
                    level="WARN",
                )
                trace.write("coverage_gate", {"turn": turn, "type": "external_research", "gap": external_gap})
                yield tool_agent_event(
                    "coverage_gate",
                    "最终报告暂缓：外部搜索/阅读覆盖不足",
                    run_id=run_id,
                    turn=turn,
                    missing_holding_research=external_gap.get("missing_holding_research", []),
                    web_read_count=state.web_read_count,
                    searched_queries=state.web_search_queries,
                )
                state.reflection_required = False
                state.messages.append({"role": "user", "content": build_external_research_gate_prompt(external_gap)})
                continue
            missing_holding_analysis = final_report_missing_holding_analysis(workspace, goal, step.final_report)
            if missing_holding_analysis:
                agent_log(
                    run_id,
                    f"final_report deferred missing_holding_analysis={len(missing_holding_analysis)}",
                    turn=turn,
                    level="WARN",
                )
                trace.write(
                    "coverage_gate",
                    {"turn": turn, "type": "holding_analysis", "missing": missing_holding_analysis},
                )
                yield tool_agent_event(
                    "coverage_gate",
                    "最终报告暂缓：报告缺少部分 ETF 的逐项建议",
                    run_id=run_id,
                    turn=turn,
                    missing_holding_analysis=missing_holding_analysis,
                )
                state.pending_final_report = normalize_report_payload(step.final_report)
                state.messages.append({"role": "user", "content": build_holding_analysis_gate_prompt(missing_holding_analysis)})
                continue
            for event in persist_final_report(
                run_id=run_id,
                turn=turn,
                trace=trace,
                workspace=workspace,
                config=config,
                model=model,
                save_snapshot=save_snapshot,
                save_report=save_report,
                final_report=step.final_report,
            ):
                yield event
            return

        events, completed = run_tool_calls(
            run_id=run_id,
            turn=turn,
            trace=trace,
            registry=registry,
            workspace=workspace,
            state=state,
            max_calls=max_calls,
            calls=step.tool_calls,
        )
        for event in events:
            yield event
        if not completed:
            return

    message = f"达到 max_tool_turns={max_turns}，Agent 未完成"
    trace.write("error", {"error": message})
    agent_log(run_id, message, level="ERROR")
    yield tool_agent_event("error", message, run_id=run_id, error=message)
