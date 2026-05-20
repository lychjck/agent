import json
import time
from typing import Any

from stock_assistant.agents.agent import save_ai_report
from stock_assistant.agents.agent_executor import execute_tool_call, tool_observation_message
from stock_assistant.agents.agent_llm import parse_agent_report
from stock_assistant.agents.agent_coverage import build_coverage_prompt
from stock_assistant.agents.agent_loop_events import agent_log, compact_for_log, tool_agent_event
from stock_assistant.agents.agent_loop_state import AgentLoopState
from stock_assistant.agents.agent_protocol import build_reflection_prompt
from stock_assistant.agents.agent_tool_batch import (
    chunked,
    execute_call_batch,
    externally_slow_tool,
)
from stock_assistant.agents.agent_tools import AgentToolSpec
from stock_assistant.agents.agent_trace import AgentTraceWriter
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.llm_tools import LlmToolCall
from stock_assistant.core.memory import agent_snapshots_have_same_facts, save_agent_snapshot
from stock_assistant.core.utils import config_bool


def run_missing_technical_gate(
    *,
    run_id: str,
    turn: int,
    trace: AgentTraceWriter,
    registry: dict[str, AgentToolSpec],
    workspace: AgentWorkspace,
    state: AgentLoopState,
    max_calls: int,
    missing_codes: list[str],
    gate_status: str,
    compact_tool_observations: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    events = [
        tool_agent_event(
            "coverage_gate",
            gate_status,
            run_id=run_id,
            turn=turn,
            missing_codes=missing_codes,
        )
    ]
    for index, code_chunk in enumerate(chunked(missing_codes, 20), start=1):
        state.tool_call_count += 1
        if state.tool_call_count > max_calls:
            message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
            trace.write("error", {"turn": turn, "error": message})
            agent_log(run_id, message, turn=turn, level="ERROR")
            events.append(tool_agent_event("error", message, run_id=run_id, error=message))
            return events, False
        call = LlmToolCall(
            id=f"auto_coverage_{turn}_{index:02d}",
            name="get_holding_technical",
            arguments={"codes": code_chunk},
        )
        trace.write("tool_call", {"turn": turn, "call": call.model_dump(), "reason": "coverage_gate"})
        events.append(
            tool_agent_event(
                "tool_call",
                f"补查缺失技术指标：{len(code_chunk)} 个标的",
                run_id=run_id,
                turn=turn,
                tool=call.name,
                arguments=call.arguments,
                auto=True,
            )
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
        events.append(
            tool_agent_event(
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
        )
        state.messages.append(tool_observation_message(call, observation, compact=compact_tool_observations))
    state.require_reflection()
    state.messages.append({"role": "user", "content": build_coverage_prompt(missing_codes)})
    state.messages.append({"role": "user", "content": build_reflection_prompt()})
    return events, True


def select_auto_web_read_url(workspace: AgentWorkspace) -> str:
    for item in workspace.external_evidence:
        if not isinstance(item, dict) or item.get("type") != "web_search_result":
            continue
        facts = item.get("facts")
        if not isinstance(facts, dict):
            continue
        url = str(facts.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def run_auto_web_read_gate(
    *,
    run_id: str,
    turn: int,
    trace: AgentTraceWriter,
    registry: dict[str, AgentToolSpec],
    workspace: AgentWorkspace,
    state: AgentLoopState,
    max_calls: int,
    compact_tool_observations: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    if "web_read" not in registry:
        return [], True
    url = select_auto_web_read_url(workspace)
    if not url:
        return [], True
    state.tool_call_count += 1
    if state.tool_call_count > max_calls:
        message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
        trace.write("error", {"turn": turn, "error": message})
        agent_log(run_id, message, turn=turn, level="ERROR")
        return [tool_agent_event("error", message, run_id=run_id, error=message)], False

    call = LlmToolCall(
        id=f"auto_web_read_{turn:02d}",
        name="web_read",
        arguments={"url": url},
    )
    trace.write("tool_call", {"turn": turn, "call": call.model_dump(), "reason": "external_research_gate"})
    events = [
        tool_agent_event(
            "tool_call",
            "外部研究缺少来源页阅读，后端自动打开一个已搜索来源",
            run_id=run_id,
            turn=turn,
            tool=call.name,
            arguments=call.arguments,
            auto=True,
        )
    ]
    started = time.monotonic()
    observation = execute_tool_call(call, registry, workspace)
    elapsed = time.monotonic() - started
    workspace.record_external_evidence(call, observation)
    trace.write("tool_observation", {"turn": turn, "observation": observation.model_dump()})
    agent_log(
        run_id,
        (
            f"auto_web_read_observation ok={observation.ok} elapsed={elapsed:.2f}s "
            f"summary={observation.summary or observation.message}"
        ),
        turn=turn,
        level="INFO" if observation.ok else "WARN",
    )
    events.append(
        tool_agent_event(
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
    )
    state.record_external_coverage(call, observation)
    state.messages.append(tool_observation_message(call, observation, compact=compact_tool_observations))
    return events, True


def run_tool_calls(
    *,
    run_id: str,
    turn: int,
    trace: AgentTraceWriter,
    registry: dict[str, AgentToolSpec],
    workspace: AgentWorkspace,
    state: AgentLoopState,
    max_calls: int,
    calls: list[LlmToolCall],
    compact_tool_observations: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    events: list[dict[str, Any]] = []
    executable_calls: list[LlmToolCall] = []
    for call in calls:
        state.tool_call_count += 1
        if state.tool_call_count > max_calls:
            message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
            trace.write("error", {"turn": turn, "error": message})
            agent_log(run_id, message, turn=turn, level="ERROR")
            events.append(tool_agent_event("error", message, run_id=run_id, error=message))
            return events, False

        trace.write("tool_call", {"turn": turn, "call": call.model_dump()})
        agent_log(
            run_id,
            f"tool_call index={state.tool_call_count}/{max_calls} name={call.name} args={compact_for_log(call.arguments)}",
            turn=turn,
        )
        events.append(
            tool_agent_event(
                "tool_call",
                f"调用工具：{call.name}",
                run_id=run_id,
                turn=turn,
                tool=call.name,
                arguments=call.arguments,
            )
        )
        executable_calls.append(call)

    if len(executable_calls) > 1 and all(externally_slow_tool(call.name) for call in executable_calls):
        agent_log(run_id, f"parallel_tool_batch count={len(executable_calls)}", turn=turn)

    for call, observation, elapsed in execute_call_batch(executable_calls, registry, workspace):
        workspace.record_external_evidence(call, observation)
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
        events.append(
            tool_agent_event(
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
        )
        state.record_external_coverage(call, observation)
        state.messages.append(tool_observation_message(call, observation, compact=compact_tool_observations))

    state.require_reflection()
    state.messages.append({"role": "user", "content": build_reflection_prompt()})
    return events, True


def persist_final_report(
    *,
    run_id: str,
    turn: int,
    trace: AgentTraceWriter,
    workspace: AgentWorkspace,
    config: dict[str, Any],
    model: str,
    save_snapshot: bool,
    save_report: bool,
    final_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    llm_context = workspace.build_llm_context()
    report = parse_agent_report(
        json.dumps(final_report or {}, ensure_ascii=False),
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
    events = [tool_agent_event("final_report", "AI 已生成最终报告", run_id=run_id, turn=turn)]

    if save_snapshot and config_bool(config.get("agent", {}).get("save_snapshots", True)):
        if agent_snapshots_have_same_facts(workspace.previous_snapshot(), snapshot):
            agent_log(run_id, "snapshot skipped reason=same_facts")
            events.append(tool_agent_event("save_snapshot", "事实数据未变化，跳过重复保存", run_id=run_id))
        else:
            save_agent_snapshot(snapshot, config)
            agent_log(run_id, "snapshot saved")
            events.append(tool_agent_event("save_snapshot", "已保存 Agent 快照", run_id=run_id))
    if save_report:
        save_ai_report(workspace.technical_results, report, model, config)
        agent_log(run_id, f"ai_report saved technical_results={len(workspace.technical_results)}")

    trace.write("done", {"turn": turn})
    agent_log(run_id, "done")
    events.append(tool_agent_event("done", "Agent 分析完成", run_id=run_id, snapshot=snapshot))
    return events
