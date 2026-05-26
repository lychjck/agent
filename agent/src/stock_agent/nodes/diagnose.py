"""诊断节点 - 拉取持仓数据，识别异常标的"""

from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from stock_agent.state import AgentState
from stock_agent.mcp_client import McpClient


def diagnose_node(state: AgentState, *, mcp: McpClient) -> dict[str, Any]:
    """
    诊断节点：
    1. 拉取完整持仓数据（account_bundle）
    2. 拉取所有场内标的的技术指标
    3. 识别异常标的（亏损超阈值、Z-Score 偏离等）
    """
    # 1. 获取持仓全景
    bundle = mcp.get_account_bundle()
    if not bundle.get("ok"):
        return {
            "messages": [HumanMessage(content=f"获取持仓数据失败: {bundle.get('message', '未知错误')}")],
            "phase": "done",
        }

    holdings = bundle.get("holdings", [])
    profile = bundle.get("portfolio_profile", {})
    classifications = bundle.get("classifications", {})

    # 2. 获取场内标的技术指标
    # 筛选有实时价格的标的（场内 ETF/股票，price > 0）
    tradable_codes = [h["code"] for h in holdings if h.get("price", 0) > 0]

    technical_data = {}
    if tradable_codes:
        tech_result = mcp.get_technical(tradable_codes)
        if tech_result.get("ok"):
            technical_data = tech_result.get("technical", {})

    # 3. 识别异常标的
    anomalies = []

    # 规则 A: 亏损超过 -8% 的标的
    loss_threshold = -8.0
    for h in holdings:
        profit_rate = h.get("profit_rate", 0)
        if profit_rate < loss_threshold:
            anomalies.append({
                "code": h["code"],
                "name": h.get("name", h["code"]),
                "reason": f"亏损 {profit_rate:.1f}%，超过 {loss_threshold}% 阈值",
                "profit_rate": profit_rate,
                "value": h.get("value", 0),
                "type": "deep_loss",
            })

    # 规则 B: 单标的集中度超过 20%
    total_value = profile.get("total_value", 1)
    for h in holdings:
        weight = h.get("value", 0) / total_value * 100 if total_value > 0 else 0
        if weight > 20:
            anomalies.append({
                "code": h["code"],
                "name": h.get("name", h["code"]),
                "reason": f"仓位占比 {weight:.1f}%，超过 20% 安全阈值",
                "weight": weight,
                "type": "concentration",
            })

    # 规则 C: 技术面异常（RSI 超买超卖、大幅回撤）
    for code, tech in technical_data.items():
        if not isinstance(tech, dict) or not tech.get("ok"):
            continue
        rsi = tech.get("rsi14")
        drawdown = tech.get("drawdown_from_120d_high_pct")

        if rsi is not None and rsi < 25:
            name = next((h["name"] for h in holdings if h["code"] == code), code)
            anomalies.append({
                "code": code,
                "name": name,
                "reason": f"RSI14={rsi:.1f}，严重超卖",
                "rsi": rsi,
                "type": "oversold",
            })
        elif rsi is not None and rsi > 75:
            name = next((h["name"] for h in holdings if h["code"] == code), code)
            anomalies.append({
                "code": code,
                "name": name,
                "reason": f"RSI14={rsi:.1f}，超买区域",
                "rsi": rsi,
                "type": "overbought",
            })

        if drawdown is not None and drawdown < -15:
            name = next((h["name"] for h in holdings if h["code"] == code), code)
            anomalies.append({
                "code": code,
                "name": name,
                "reason": f"距120日高点回撤 {drawdown:.1f}%",
                "drawdown": drawdown,
                "type": "drawdown",
            })

    # 去重（同一标的可能触发多条规则）
    seen_codes = set()
    unique_anomalies = []
    for a in anomalies:
        key = (a["code"], a["type"])
        if key not in seen_codes:
            seen_codes.add(key)
            unique_anomalies.append(a)

    # 构建诊断摘要消息
    summary_parts = [
        f"持仓诊断完成：共 {len(holdings)} 只标的，总市值 ¥{total_value:,.0f}",
        f"发现 {len(unique_anomalies)} 个异常信号",
    ]
    if profile.get("observations"):
        summary_parts.append(f"组合风险提示: {'; '.join(profile['observations'])}")

    return {
        "holdings": holdings,
        "portfolio_profile": profile,
        "classifications": classifications,
        "anomalies": unique_anomalies,
        "messages": [HumanMessage(content="\n".join(summary_parts))],
        "phase": "investigate" if unique_anomalies else "report",
    }
