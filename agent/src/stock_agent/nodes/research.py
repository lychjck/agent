"""单标的 / 单主题研究子节点。

通过 Send 调度时，每个子节点收到的 state 是 Send 携带的 dict（不是全局 AgentState）。
子节点返回 {"investigations": [...]}，由 state 上的 add reducer 自动合并。
"""

from __future__ import annotations

from typing import Any

from stock_agent.mcp_client import McpClient
from stock_agent.nodes._safe import safe_node


@safe_node("research_holding")
def research_holding(payload: dict[str, Any], *, mcp: McpClient) -> dict[str, Any]:
    """对单个核心标的执行：ETF 成分股 + 个股/ETF 资讯"""
    code = payload.get("code", "")
    name = payload.get("name", code)
    weight = payload.get("weight_pct", 0) or 0
    is_etf = bool(payload.get("is_etf"))

    constituents: list[dict[str, Any]] | None = None
    if is_etf:
        res = mcp.get_etf_constituents([code])
        if res.get("ok"):
            entry = (res.get("results") or {}).get(code, {})
            if entry.get("ok"):
                constituents = (entry.get("constituents") or [])[:6]

    news: list[dict[str, Any]] | None = None
    q = f"{name} ({code}) 跟踪指数 2026年 驱动因素 研报"
    res = mcp.web_search(q, max_results=4)
    if res.get("ok"):
        news = res.get("results") or []

    item = {
        "type": "holding_research",
        "code": code,
        "name": name,
        "weight_pct": round(weight, 2),
        "constituents": constituents,
        "news": news,
    }
    return {"investigations": [item]}


@safe_node("research_theme")
def research_theme(payload: dict[str, Any], *, mcp: McpClient) -> dict[str, Any]:
    """对单个主题执行行业级 web_search"""
    theme = payload.get("theme", "")
    if not theme:
        return {}

    q = f"{theme} 主题 ETF 2026年 趋势 宏观前景"
    res = mcp.web_search(q, max_results=4)
    news = res.get("results") if res.get("ok") else None

    item = {
        "type": "theme_research",
        "theme": theme,
        "news": news,
    }
    return {"investigations": [item]}
