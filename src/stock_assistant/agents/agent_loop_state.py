import json
import re
from dataclasses import dataclass, field
from typing import Any

from stock_assistant.agents.agent_executor import ToolObservation
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.llm_tools import LlmToolCall


MESSAGE_COMPACTION_NOTICE = "[context_compacted]"


def message_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(str(message.get("content", ""))) for message in messages)


def _strip_json_markdown(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    if stripped.startswith("---"):
        stripped = stripped[3:].strip()
    return stripped


def _short_json_payload(value: Any, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...[truncated]"


def _compact_observation_content(content: str, max_chars: int) -> str:
    marker = "observation JSON:\n"
    if marker not in content:
        return content[:max_chars].rstrip() + "...[truncated]"
    prefix, raw_json = content.split(marker, 1)
    try:
        payload = json.loads(raw_json)
    except Exception:  # noqa: BLE001
        head = "\n".join(line for line in prefix.splitlines() if line.strip())[:600]
        return f"{MESSAGE_COMPACTION_NOTICE} 历史工具 observation 已截断。\n{head}\npreview: {raw_json[:max_chars]}"

    result = payload.get("result") if isinstance(payload, dict) else {}
    compact: dict[str, Any] = {
        "call_id": payload.get("call_id", ""),
        "tool_name": payload.get("tool_name", ""),
        "ok": payload.get("ok", False),
        "summary": payload.get("summary", ""),
        "error_type": payload.get("error_type", ""),
        "message": payload.get("message", ""),
    }
    if isinstance(result, dict):
        if isinstance(result.get("technical"), dict):
            compact["result"] = {
                "summary": result.get("summary", ""),
                "technical_codes": list(result["technical"])[:80],
                "llm_compacted": True,
            }
        elif isinstance(result.get("results"), list):
            compact["result"] = {
                "summary": result.get("summary", ""),
                "result_count": len(result.get("results") or []),
                "evidence_refs": result.get("evidence_refs", []),
                "truncated": True,
            }
        else:
            compact["result"] = {
                key: value
                for key, value in result.items()
                if key in {"summary", "count", "source", "evidence_refs", "target_codes", "queries"}
            }
    return (
        f"{MESSAGE_COMPACTION_NOTICE} 历史工具 observation 已压缩，仅保留摘要和覆盖信息。\n"
        f"{marker}{_short_json_payload(compact, max_chars)}"
    )


def _compact_assistant_content(content: str, max_chars: int) -> str:
    try:
        payload = json.loads(_strip_json_markdown(content))
    except Exception:  # noqa: BLE001
        return f"{MESSAGE_COMPACTION_NOTICE} 历史 assistant 输出已截断。\n{content[:max_chars]}"
    if not isinstance(payload, dict):
        return f"{MESSAGE_COMPACTION_NOTICE} 历史 assistant 输出已截断。\n{content[:max_chars]}"
    compact: dict[str, Any] = {
        "type": payload.get("type"),
        "reasoning_summary": payload.get("reasoning_summary", ""),
    }
    thinking = payload.get("thinking_trace")
    if isinstance(thinking, dict):
        compact["thinking_trace"] = {
            key: thinking.get(key)
            for key in ("satisfied_needs", "unsatisfied_needs", "next_step")
            if thinking.get(key)
        }
    if isinstance(payload.get("observation_reflection"), dict):
        reflection = payload["observation_reflection"]
        compact["observation_reflection"] = {
            key: reflection.get(key)
            for key in ("satisfied_needs", "unsatisfied_needs", "next_action", "coverage_notes")
            if reflection.get(key)
        }
    if isinstance(payload.get("tool_calls"), list):
        compact["tool_calls"] = [
            {"name": item.get("name"), "arguments": item.get("arguments", {})}
            for item in payload["tool_calls"][:10]
            if isinstance(item, dict)
        ]
    if isinstance(payload.get("report"), dict):
        report = payload["report"]
        compact["report"] = {
            "summary": report.get("summary", {}),
            "holding_analysis_count": len(report.get("holding_analysis") or []),
        }
    return (
        f"{MESSAGE_COMPACTION_NOTICE} 历史 assistant 输出已压缩，仅保留执行状态。\n"
        f"{_short_json_payload(compact, max_chars)}"
    )


def compact_message_for_llm(message: dict[str, str], max_chars: int = 1400) -> dict[str, str]:
    content = str(message.get("content", ""))
    if content.startswith(MESSAGE_COMPACTION_NOTICE) or len(content) <= max_chars:
        return message
    role = str(message.get("role", "user"))
    if "工具调用结果 observation" in content and "observation JSON:" in content:
        content = _compact_observation_content(content, max_chars)
    elif role == "assistant":
        content = _compact_assistant_content(content, max_chars)
    else:
        content = f"{MESSAGE_COMPACTION_NOTICE} 历史消息已截断。\n{content[:max_chars]}"
    return {**message, "content": content}


@dataclass
class AgentLoopState:
    messages: list[dict[str, str]]
    tool_call_count: int = 0
    reflection_required: bool = False
    reflection_seen: bool = False
    last_reflection: dict[str, Any] | None = None
    web_search_queries: list[str] = field(default_factory=list)
    web_search_target_codes: list[str] = field(default_factory=list)
    web_read_count: int = 0
    pending_final_report: dict[str, Any] | None = None
    external_evidence: list[dict[str, Any]] = field(default_factory=list)
    # 循环检测：记录 coverage_gate 连续触发次数和上次缺失的 codes
    _coverage_gate_consecutive: int = field(default=0, repr=False)
    _coverage_gate_last_missing: set[str] = field(default_factory=set, repr=False)
    # 重复 URL 去重
    _web_read_urls: set[str] = field(default_factory=set, repr=False)
    # 重复 tool_calls 模式检测（记录最近几轮的 tool_calls 签名）
    _recent_tool_signatures: list[str] = field(default_factory=list, repr=False)

    @classmethod
    def from_resume(cls, state: dict[str, Any], initial_messages: list[dict[str, str]]) -> "AgentLoopState":
        return cls(
            messages=list(state.get("messages") or initial_messages),
            tool_call_count=int(state.get("tool_call_count", 0) or 0),
            reflection_required=bool(state.get("reflection_required", False)),
            reflection_seen=bool(state.get("reflection_seen", False)),
            last_reflection=state.get("last_reflection") if isinstance(state.get("last_reflection"), dict) else None,
            web_search_queries=[str(item) for item in state.get("web_search_queries", [])],
            web_search_target_codes=[str(item) for item in state.get("web_search_target_codes", [])],
            web_read_count=int(state.get("web_read_count", 0) or 0),
            pending_final_report=(
                state.get("pending_final_report") if isinstance(state.get("pending_final_report"), dict) else None
            ),
            external_evidence=[
                item for item in state.get("external_evidence", [])
                if isinstance(item, dict)
            ],
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
            "web_search_target_codes": self.web_search_target_codes,
            "web_read_count": self.web_read_count,
            "pending_final_report": self.pending_final_report or {},
            "external_evidence": self.external_evidence,
        }

    def compact_messages_for_llm(
        self,
        *,
        max_chars: int = 60000,
        keep_recent: int = 8,
        force: bool = False,
    ) -> int:
        if not force and message_chars(self.messages) <= max_chars:
            return 0
        if len(self.messages) <= 3:
            return 0
        compacted = 0
        recent_start = max(2, len(self.messages) - keep_recent)
        next_messages: list[dict[str, str]] = []
        for index, message in enumerate(self.messages):
            if index < 2:
                next_messages.append(message)
                continue
            if index >= recent_start and not force:
                next_messages.append(message)
                continue
            compacted_message = compact_message_for_llm(message)
            if compacted_message.get("content") != message.get("content"):
                compacted += 1
            next_messages.append(compacted_message)
        self.messages = next_messages

        if message_chars(self.messages) <= max_chars or keep_recent <= 3:
            return compacted
        recent_start = max(2, len(self.messages) - max(3, keep_recent // 2))
        next_messages = []
        for index, message in enumerate(self.messages):
            if index < 2 or index >= recent_start:
                next_messages.append(message)
                continue
            compacted_message = compact_message_for_llm(message, max_chars=900)
            if compacted_message.get("content") != message.get("content"):
                compacted += 1
            next_messages.append(compacted_message)
        self.messages = next_messages
        return compacted

    def accept_reflection(self, reflection: dict[str, Any] | None) -> dict[str, Any]:
        self.reflection_required = False
        self.reflection_seen = True
        self.last_reflection = reflection or {}
        return self.last_reflection

    def require_reflection(self) -> None:
        self.reflection_required = True

    def record_external_coverage(self, call: LlmToolCall, observation: ToolObservation) -> None:
        if observation.ok and call.name == "web_search":
            target_codes = (observation.result or {}).get("target_codes")
            if isinstance(target_codes, list):
                for item in target_codes:
                    code = str(item).strip()
                    if code and code not in self.web_search_target_codes:
                        self.web_search_target_codes.append(code)
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
                self._extract_code_from_url(str((call.arguments.get("options") or {}).get("url", "")))
        if observation.ok and call.name == "web_read":
            self.web_read_count += 1
            self._extract_code_from_url(str(call.arguments.get("url", "")))

    def _extract_code_from_url(self, url: str) -> None:
        """从 URL 中提取基金/ETF 代码并注册到 web_search_target_codes，解决 web_read 不注册覆盖的问题。"""
        if not url:
            return
        match = re.search(r"/(\d{6})(?:\.s?html|\.htm|/)", url)
        if match:
            code = match.group(1)
            if code not in self.web_search_target_codes:
                self.web_search_target_codes.append(code)
