"""诊断节点 - 拉取持仓数据，识别异常标的"""

from typing import Any

from langchain_core.messages import HumanMessage

from stock_agent.state import AgentState
from stock_agent.mcp_client import McpClient
from stock_agent.nodes._safe import safe_node


# stock_mcp Holding 真实字段：value(市值) / profit_rate(百分比) / asset_class / sector
# 没有 weight_pct，需要本地计算后回写


def _compute_weight(holding: dict[str, Any], total_value: float) -> float:
    """统一计算权重百分比"""
    if total_value <= 0:
        return 0.0
    return (holding.get("value", 0) or 0) / total_value * 100


def _enrich_holdings(holdings: list[dict[str, Any]], total_value: float) -> None:
    """把 weight_pct 写回 holdings dict，便于下游节点直接使用"""
    for h in holdings:
        h["weight_pct"] = round(_compute_weight(h, total_value), 4)


@safe_node("diagnose")
def diagnose_node(state: AgentState, *, mcp: McpClient) -> dict[str, Any]:
    """
    诊断节点：
    1. 拉取持仓 / 画像 / 分类（三个独立工具，单点失败不影响其他）
    2. 拉取核心场内标的的技术指标
    3. 按规则识别异常（深亏 / 集中度 / RSI 超买超卖 / 大幅回撤）
    """
    # 1. 持仓
    holdings_res = mcp.call_tool("stock_get_current_holdings")
    if not holdings_res.get("ok"):
        err = holdings_res.get("message", "未知错误")
        return {
            "holdings": [],
            "portfolio_profile": {},
            "classifications": {},
            "technical_data": {},
            "anomalies": [],
            "messages": [HumanMessage(content=f"获取当前持仓失败: {err}")],
        }
    holdings = holdings_res.get("holdings", [])

    # 2. 组合画像
    profile_res = mcp.call_tool("stock_get_portfolio_profile")
    profile = profile_res.get("portfolio", {}) if profile_res.get("ok") else {}
    if not profile.get("total_value"):
        profile = dict(profile) if profile else {}
        profile["total_value"] = sum(h.get("value", 0) or 0 for h in holdings)

    total_value = profile.get("total_value", 0) or 0
    _enrich_holdings(holdings, total_value)

    # 3. 资产分类
    classifications: dict[str, Any] = {}
    codes = [h["code"] for h in holdings if h.get("code")]
    if codes:
        cls_res = mcp.call_tool("stock_get_classification", {"codes": codes})
        if cls_res.get("ok"):
            classifications = cls_res.get("classifications", {}) or {}

    # 4. 规则 A: 亏损超 -8% / 规则 B: 单标的集中度 > 20%
    anomalies: list[dict[str, Any]] = []
    loss_threshold = -8.0
    for h in holdings:
        profit_rate = h.get("profit_rate", 0) or 0
        if profit_rate < loss_threshold:
            anomalies.append({
                "code": h["code"],
                "name": h.get("name", h["code"]),
                "reason": f"亏损 {profit_rate:.1f}%，超过 {loss_threshold}% 阈值",
                "profit_rate": profit_rate,
                "value": h.get("value", 0) or 0,
                "type": "deep_loss",
            })

    for h in holdings:
        weight = h.get("weight_pct", 0) or 0
        if weight > 20:
            anomalies.append({
                "code": h["code"],
                "name": h.get("name", h["code"]),
                "reason": f"仓位占比 {weight:.1f}%，超过 20% 安全阈值",
                "weight": weight,
                "type": "concentration",
            })

    # 5. 技术指标：覆盖核心场内标的（权重>=1% 或已经是异常）
    anomaly_codes = {a["code"] for a in anomalies}
    tradable_codes: list[str] = []
    for h in holdings:
        code = h.get("code")
        if not code or len(code) != 6:
            continue
        weight = h.get("weight_pct", 0) or 0
        if weight >= 1.0 or code in anomaly_codes:
            tradable_codes.append(code)

    technical_data: dict[str, Any] = {}
    if tradable_codes:
        tech_res = mcp.get_technical(tradable_codes)
        if tech_res.get("ok"):
            for code, item in (tech_res.get("results", {}) or {}).items():
                if isinstance(item, dict) and item.get("ok"):
                    technical_data[code] = item.get("indicators", {}) or {}

    # 把 technical / classification 内嵌进 holdings，供主项目 fallback 使用
    for h in holdings:
        code = h.get("code")
        if not code:
            continue
        tech = technical_data.get(code)
        if tech:
            h["technical"] = tech
        cls = classifications.get(code)
        if cls:
            h["classification"] = cls

    # 6. 规则 C: RSI 超买超卖 / 大幅回撤
    name_by_code = {h["code"]: h.get("name", h["code"]) for h in holdings if h.get("code")}
    for code, tech in technical_data.items():
        if not isinstance(tech, dict):
            continue
        rsi = tech.get("rsi14")
        # stock_mcp 真实字段是 drawdown_120d（已是百分比，负值）
        drawdown = tech.get("drawdown_120d")

        if rsi is not None and rsi < 25:
            anomalies.append({
                "code": code,
                "name": name_by_code.get(code, code),
                "reason": f"RSI14={rsi:.1f}，严重超卖",
                "rsi": rsi,
                "type": "oversold",
            })
        elif rsi is not None and rsi > 75:
            anomalies.append({
                "code": code,
                "name": name_by_code.get(code, code),
                "reason": f"RSI14={rsi:.1f}，超买区域",
                "rsi": rsi,
                "type": "overbought",
            })

        if drawdown is not None and drawdown < -15:
            anomalies.append({
                "code": code,
                "name": name_by_code.get(code, code),
                "reason": f"距120日高点回撤 {drawdown:.1f}%",
                "drawdown": drawdown,
                "type": "drawdown",
            })

    # 去重
    seen: set[tuple[str, str]] = set()
    unique_anomalies: list[dict[str, Any]] = []
    for a in anomalies:
        key = (a["code"], a["type"])
        if key in seen:
            continue
        seen.add(key)
        unique_anomalies.append(a)

    # 摘要
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
        "technical_data": technical_data,
        "anomalies": unique_anomalies,
        "messages": [HumanMessage(content="\n".join(summary_parts))],
    }
