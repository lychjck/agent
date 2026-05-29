"""渲染节点：校验报告 → 渲染 Markdown → 落盘"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from stock_agent.mcp_client import McpClient
from stock_agent.nodes._safe import safe_node
from stock_agent.render import render_markdown
from stock_agent.schema import validate_agent_report
from stock_agent.state import AgentState


# agent/reports
_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "reports"


def _build_evidence_index(
    holdings: list[dict[str, Any]],
    investigations: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for h in holdings:
        code = h.get("code")
        if not code:
            continue
        index[f"holding:{code}:technical"] = {"type": "technical"}
        index[f"holding:{code}:classification"] = {"type": "classification"}
    for inv in investigations:
        if inv.get("type") == "holding_research":
            code = inv.get("code")
            if code:
                index[f"holding:{code}:research"] = {"type": "research"}
    return index


@safe_node("render")
def render_node(state: AgentState, *, mcp: McpClient) -> dict[str, Any]:
    holdings = state.get("holdings", []) or []
    profile = state.get("portfolio_profile", {}) or {}
    technical = state.get("technical_data", {}) or {}
    investigations = state.get("investigations", []) or []
    report_data = state.get("report_data", {}) or {}

    evidence_index = _build_evidence_index(holdings, investigations)

    # 校验自愈：失败时直接 fallback 到原始 report_data
    try:
        clean_report = validate_agent_report(
            report_data,
            evidence_index=evidence_index,
            holdings=holdings,
        )
    except Exception:
        clean_report = report_data or {}

    md = render_markdown(
        clean_report,
        holdings=holdings,
        profile=profile,
        technical=technical,
    )

    today_str = dt.date.today().strftime("%Y-%m-%d")

    # 落本地盘
    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (_REPORTS_DIR / f"{today_str}-langgraph-diagnostic-report.md").write_text(md, encoding="utf-8")
    except OSError:
        pass

    # 异步保存快照（失败不影响渲染）
    try:
        mcp.save_snapshot({
            "report_data": clean_report,
            "rendered_report": md,
            "generated_at": dt.datetime.now().isoformat(),
        })
    except Exception:  # noqa: BLE001
        pass

    return {"report": md, "messages": [AIMessage(content=md)]}
