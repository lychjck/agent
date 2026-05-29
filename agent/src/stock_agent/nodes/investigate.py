"""探案分发节点：返回 Send 列表让 LangGraph 把每个 holding/theme 拆成并行子任务"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Send

from stock_agent.state import AgentState


def _is_etf_or_lof(code: str) -> bool:
    return len(code) == 6 and code[0] in ("1", "5")


def investigate_dispatch(state: AgentState) -> dict[str, Any]:
    """投出 fan-out 任务清单。Send 在 dispatch_to_research 条件边里发出。

    本节点本身只写一条 messages 摘要，真正的并发研究由 research_holding /
    research_theme 子节点完成（被 dispatch_to_research 通过 Send 调度）。
    """
    holdings = state.get("holdings", [])
    profile = state.get("portfolio_profile", {})

    if not holdings:
        return {"messages": [HumanMessage(content="无持仓数据，跳过研究灌注。")]}

    total_value = profile.get("total_value", 0) or 0

    # 核心标的（权重 >= 1.0%）
    core = []
    for h in holdings:
        weight = h.get("weight_pct")
        if weight is None and total_value > 0:
            weight = (h.get("value", 0) or 0) / total_value * 100
        if weight is not None and weight >= 1.0:
            core.append(h)

    # 前 3 大暴露主题
    by_sector = profile.get("by_sector", {}) or {}
    themes = [
        name for name, _ in sorted(by_sector.items(), key=lambda x: -x[1])[:3]
        if name and name not in ("unknown", "未知", "Unknown")
    ]

    summary = (
        f"研究分发：{len(core)} 个核心标的（含 {sum(1 for h in core if _is_etf_or_lof(h['code']))} 个 ETF）"
        f"+ {len(themes)} 个主题"
    )

    return {"messages": [HumanMessage(content=summary)]}


def dispatch_to_research(state: AgentState) -> list[Send] | str:
    """条件边：返回 Send 列表（fan-out）或者直接路由到 'report' 节点

    LangGraph 支持 conditional_edges 返回 list[Send] 或 str。
    没有研究目标时直接路由到 report，避免 fan-out 空挂。
    """
    holdings = state.get("holdings", [])
    profile = state.get("portfolio_profile", {})
    if not holdings:
        return "report"

    total_value = profile.get("total_value", 0) or 0

    core = []
    for h in holdings:
        weight = h.get("weight_pct")
        if weight is None and total_value > 0:
            weight = (h.get("value", 0) or 0) / total_value * 100
        if weight is not None and weight >= 1.0:
            core.append(h)

    by_sector = profile.get("by_sector", {}) or {}
    themes = [
        name for name, _ in sorted(by_sector.items(), key=lambda x: -x[1])[:3]
        if name and name not in ("unknown", "未知", "Unknown")
    ]

    sends: list[Send] = []
    for h in core:
        sends.append(Send("research_holding", {
            "code": h["code"],
            "name": h.get("name", h["code"]),
            "weight_pct": h.get("weight_pct", 0),
            "is_etf": _is_etf_or_lof(h["code"]),
        }))
    for theme in themes:
        sends.append(Send("research_theme", {"theme": theme}))

    return sends if sends else "report"
