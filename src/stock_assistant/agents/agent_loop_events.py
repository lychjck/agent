import datetime as dt
import json
from typing import Any

from stock_assistant.core.utils import log


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
