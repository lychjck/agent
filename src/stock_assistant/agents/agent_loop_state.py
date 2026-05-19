from dataclasses import dataclass, field
from typing import Any

from stock_assistant.agents.agent_executor import ToolObservation
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.llm_tools import LlmToolCall


@dataclass
class AgentLoopState:
    messages: list[dict[str, str]]
    tool_call_count: int = 0
    reflection_required: bool = False
    reflection_seen: bool = False
    last_reflection: dict[str, Any] | None = None
    web_search_queries: list[str] = field(default_factory=list)
    web_read_count: int = 0
    pending_final_report: dict[str, Any] | None = None

    @classmethod
    def from_resume(cls, state: dict[str, Any], initial_messages: list[dict[str, str]]) -> "AgentLoopState":
        return cls(
            messages=list(state.get("messages") or initial_messages),
            tool_call_count=int(state.get("tool_call_count", 0) or 0),
            reflection_required=bool(state.get("reflection_required", False)),
            reflection_seen=bool(state.get("reflection_seen", False)),
            last_reflection=state.get("last_reflection") if isinstance(state.get("last_reflection"), dict) else None,
            web_search_queries=[str(item) for item in state.get("web_search_queries", [])],
            web_read_count=int(state.get("web_read_count", 0) or 0),
            pending_final_report=(
                state.get("pending_final_report") if isinstance(state.get("pending_final_report"), dict) else None
            ),
        )

    def checkpoint(self, workspace: AgentWorkspace, next_turn: int) -> dict[str, Any]:
        return {
            "messages": self.messages,
            "cached_results": workspace.technical_results,
            "next_turn": next_turn,
            "tool_call_count": self.tool_call_count,
            "reflection_required": self.reflection_required,
            "reflection_seen": self.reflection_seen,
            "last_reflection": self.last_reflection or {},
            "web_search_queries": self.web_search_queries,
            "web_read_count": self.web_read_count,
            "pending_final_report": self.pending_final_report or {},
        }

    def accept_reflection(self, reflection: dict[str, Any] | None) -> dict[str, Any]:
        self.reflection_required = False
        self.reflection_seen = True
        self.last_reflection = reflection or {}
        return self.last_reflection

    def require_reflection(self) -> None:
        self.reflection_required = True

    def record_external_coverage(self, call: LlmToolCall, observation: ToolObservation) -> None:
        if observation.ok and call.name == "web_search":
            queries = (observation.result or {}).get("queries")
            if isinstance(queries, list):
                for item in queries:
                    query = str(item).strip()
                    if query:
                        self.web_search_queries.append(query)
            else:
                query = str((observation.result or {}).get("query") or call.arguments.get("query") or "").strip()
                if query:
                    self.web_search_queries.append(query)
        if observation.ok and call.name == "opencli_command":
            site = str(call.arguments.get("site", "")).strip()
            command = str(call.arguments.get("command", "")).strip()
            positionals = call.arguments.get("positionals") or []
            options = call.arguments.get("options") or {}
            query = " ".join(
                [site, command]
                + [str(item) for item in positionals if str(item).strip()]
                + [str(value) for value in options.values() if str(value).strip()]
            ).strip()
            if query:
                self.web_search_queries.append(query)
            result_site = str((observation.result or {}).get("site") or site)
            result_command = str((observation.result or {}).get("command") or command)
            if result_site == "web" and result_command == "read":
                self.web_read_count += 1
        if observation.ok and call.name == "web_read":
            self.web_read_count += 1
