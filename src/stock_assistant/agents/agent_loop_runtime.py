from dataclasses import dataclass
from typing import Any

from stock_assistant.agents.agent_loop_events import agent_run_id
from stock_assistant.agents.agent_loop_state import AgentLoopState
from stock_assistant.agents.agent_protocol import build_initial_agent_messages
from stock_assistant.agents.agent_tools import AgentToolSpec, build_agent_tool_registry, tool_schemas
from stock_assistant.agents.agent_trace import AgentTraceWriter
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.utils import config_bool


@dataclass
class AgentLoopRuntime:
    config: dict[str, Any]
    goal: str
    model_override: str | None
    save_snapshot: bool
    save_report: bool
    resume_state: dict[str, Any] | None
    run_id: str
    model: str
    trace: AgentTraceWriter
    workspace: AgentWorkspace
    registry: dict[str, AgentToolSpec]
    schemas: list[dict[str, Any]]
    state: AgentLoopState
    max_turns: int
    start_turn: int
    max_calls: int


def build_agent_loop_runtime(
    config: dict[str, Any],
    *,
    goal: str,
    cached_results: list[dict[str, Any]] | None,
    model_override: str | None,
    save_snapshot: bool,
    save_report: bool,
    resume_state: dict[str, Any] | None,
) -> AgentLoopRuntime:
    run_id = agent_run_id()
    model = model_override or config.get("llm", {}).get("model", "unknown")
    trace = AgentTraceWriter(config, run_id)
    resume_payload = resume_state or {}
    workspace = AgentWorkspace(config, cached_results=resume_payload.get("cached_results") or cached_results)
    registry = build_agent_tool_registry(config)
    schemas = tool_schemas(registry)
    use_native_tools = config_bool(config.get("agent", {}).get("use_native_tools", False))
    initial_messages = build_initial_agent_messages(goal, schemas, use_native_tools=use_native_tools)
    state = AgentLoopState.from_resume(resume_payload, initial_messages)
    max_turns = int(config.get("agent", {}).get("max_tool_turns", 12) or 12)
    start_turn = int(resume_payload.get("next_turn", 1) or 1)
    if resume_state:
        max_turns = start_turn + max_turns
    max_calls = int(config.get("agent", {}).get("max_tool_calls", 16) or 16)
    return AgentLoopRuntime(
        config=config,
        goal=goal,
        model_override=model_override,
        save_snapshot=save_snapshot,
        save_report=save_report,
        resume_state=resume_state,
        run_id=run_id,
        model=model,
        trace=trace,
        workspace=workspace,
        registry=registry,
        schemas=schemas,
        state=state,
        max_turns=max_turns,
        start_turn=start_turn,
        max_calls=max_calls,
    )
