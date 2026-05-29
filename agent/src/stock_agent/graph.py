"""LangGraph 状态图

图结构：

    START
      │
      ▼
   diagnose ── errors? ──▶ error_handler ── END
      │ no
      ▼
   should_investigate? ─────────┐
      │ yes                     │ no
      ▼                         │
   investigate                  │
      │                         │
      └─Send─▶ research_holding ─┤   (并行 fan-out)
      └─Send─▶ research_theme ───┤
                                 │
                                 ▼
                              report ── errors? ──▶ error_handler ── END
                                 │ no
                                 ▼
                              render ──▶ END
"""

from __future__ import annotations

from functools import partial
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from stock_agent.mcp_client import McpClient
from stock_agent.nodes import (
    diagnose_node,
    dispatch_to_research,
    error_handler_node,
    investigate_dispatch,
    render_node,
    report_node,
    research_holding,
    research_theme,
)
from stock_agent.state import AgentState


# ---------- 条件边 ----------

def _has_errors(state: AgentState) -> Literal["error", "ok"]:
    return "error" if state.get("errors") else "ok"


def _should_investigate(state: AgentState) -> Literal["investigate", "report"]:
    """没有持仓直接跳过研究；有研究价值才进 investigate"""
    holdings = state.get("holdings", []) or []
    if not holdings:
        return "report"
    profile = state.get("portfolio_profile", {}) or {}
    total_value = profile.get("total_value", 0) or 0
    # 至少要有一个权重 >=1% 的标的或一个主题暴露才值得研究
    has_core = any(
        (h.get("weight_pct") or 0) >= 1.0
        or (total_value > 0 and (h.get("value", 0) or 0) / total_value * 100 >= 1.0)
        for h in holdings
    )
    return "investigate" if has_core else "report"


# ---------- 图构建 ----------

def build_graph(mcp: McpClient, llm: Any) -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("diagnose", partial(diagnose_node, mcp=mcp))
    g.add_node("investigate", investigate_dispatch)
    g.add_node("research_holding", partial(research_holding, mcp=mcp))
    g.add_node("research_theme", partial(research_theme, mcp=mcp))
    g.add_node("report", partial(report_node, llm=llm))
    g.add_node("render", partial(render_node, mcp=mcp))
    g.add_node("error_handler", error_handler_node)

    g.add_edge(START, "diagnose")

    # diagnose 之后：错了走兜底，否则判断要不要 investigate
    g.add_conditional_edges(
        "diagnose",
        lambda s: "error" if s.get("errors") else _should_investigate(s),
        {
            "error": "error_handler",
            "investigate": "investigate",
            "report": "report",
        },
    )

    # investigate 用条件边发 Send 完成 fan-out
    g.add_conditional_edges(
        "investigate",
        dispatch_to_research,
        ["research_holding", "research_theme", "report"],
    )

    # 没有任何研究目标时（dispatch 返回空），LangGraph 会直接跳过 fan-out 走默认 fallback；
    # 这里给 research_* 子节点统一连到 report，让 fan-in 自然完成
    g.add_edge("research_holding", "report")
    g.add_edge("research_theme", "report")

    # report 之后：错了走兜底，否则进 render
    g.add_conditional_edges(
        "report",
        _has_errors,
        {"error": "error_handler", "ok": "render"},
    )

    g.add_edge("render", END)
    g.add_edge("error_handler", END)

    return g


def compile_graph(mcp: McpClient, llm: Any):
    return build_graph(mcp, llm).compile()
