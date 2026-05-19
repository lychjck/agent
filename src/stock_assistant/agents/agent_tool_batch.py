import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from stock_assistant.agents.agent_executor import ToolObservation, execute_tool_call
from stock_assistant.agents.agent_tools import AgentToolSpec
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.llm_tools import LlmToolCall


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


def externally_slow_tool(name: str) -> bool:
    return name in {"web_search", "web_read", "opencli_command", "web_fetch"}


def execute_call_batch(
    calls: list[LlmToolCall],
    registry: dict[str, AgentToolSpec],
    workspace: AgentWorkspace,
) -> list[tuple[LlmToolCall, ToolObservation, float]]:
    if len(calls) <= 1 or not all(externally_slow_tool(call.name) for call in calls):
        output: list[tuple[LlmToolCall, ToolObservation, float]] = []
        for call in calls:
            started = time.monotonic()
            observation = execute_tool_call(call, registry, workspace)
            output.append((call, observation, time.monotonic() - started))
        return output

    output_by_id: dict[str, tuple[LlmToolCall, ToolObservation, float]] = {}
    max_workers = min(6, len(calls))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for call in calls:
            started = time.monotonic()
            future = executor.submit(execute_tool_call, call, registry, workspace)
            future_map[future] = (call, started)
        for future in as_completed(future_map):
            call, started = future_map[future]
            try:
                observation = future.result()
            except Exception as exc:  # noqa: BLE001
                observation = ToolObservation(
                    call_id=call.id,
                    tool_name=call.name,
                    ok=False,
                    error_type="tool_error",
                    message=str(exc),
                )
            output_by_id[call.id] = (call, observation, time.monotonic() - started)
    return [output_by_id[call.id] for call in calls]
