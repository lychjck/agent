"""把校验后的报告 dict 渲染为 Markdown"""

from __future__ import annotations

import datetime as dt
from typing import Any


_ACTION_BADGE = {
    "buy":               ("🔴 分批加仓", "🔴"),
    "reduce":            ("🔵 分批减仓", "🔵"),
    "hold":              ("🟢 继续持有", "🟢"),
    "watch":             ("🟡 维持观察", "🟡"),
    "rebalance":         ("🟣 再平衡",   "🟣"),
    "classify_required": ("⚪ 分类缺失", "⚪"),
}

_ACTION_PRIORITY = {
    "buy": 0, "reduce": 1, "rebalance": 2, "hold": 3, "watch": 4, "classify_required": 5,
}


def render_markdown(
    report: dict[str, Any],
    *,
    holdings: list[dict[str, Any]],
    profile: dict[str, Any],
    technical: dict[str, Any],
    today: dt.date | None = None,
) -> str:
    today = today or dt.date.today()
    today_str = today.strftime("%Y-%m-%d")

    parts: list[str] = []
    parts.append(f"# 📊 个人持仓深度诊断与调仓优化日报 ({today_str})")
    parts.append("\n> 基于【本地多维指标 + 外部并发研究】生成。诊断结论非投资建议。\n")

    # 概览
    summary = report.get("summary", {}) or {}
    score = summary.get("health_score") or 75
    status = (summary.get("status") or "watch").upper()
    brief = summary.get("brief") or "组合整体平稳，部分核心暴露需要关注。"
    score_dot = "🟢" if score >= 80 else ("🟡" if score >= 60 else "🔴")

    parts.append("## ⚖️ 组合资产配置与健康诊断")
    parts.append("\n| 诊断项 | 指标 | 状态 | 简评 |")
    parts.append("| :--- | :--- | :--- | :--- |")
    parts.append(f"| **组合健康得分** | **{score} 分** / 100 | {score_dot} {status} | {brief} |")
    parts.append(
        f"| **持仓总市值** | **¥{profile.get('total_value', 0):,.2f}** | 📈 正常 | "
        f"共 **{len(holdings)}** 只活跃标的 |"
    )
    by_class = profile.get("by_asset_class", {})
    if by_class:
        cls_str = "  ".join(
            f"`{cls}: {pct * 100:.1f}%`"
            for cls, pct in sorted(by_class.items(), key=lambda x: -x[1])
        )
        parts.append(f"| **大类资产暴露** | {cls_str} | ⚖️ 配置 | 资产配置集中度正常 |")
    parts.append("")

    # diagnosis
    diagnosis = report.get("diagnosis") or []
    if diagnosis:
        parts.append("## 🔍 核心风险因子诊断")
        for d in diagnosis:
            sev = (d.get("severity") or "medium").upper()
            badge = "🚨 CRITICAL" if sev == "CRITICAL" else ("⚠️ WARNING" if sev == "HIGH" else "💡 INFO")
            parts.append(f"> ### {badge} | {d.get('title', '')}")
            parts.append(f"> {d.get('explanation', '')}")
            if d.get("evidence_refs"):
                parts.append(f"> \n> *证据：{', '.join(d['evidence_refs'])}*")
            parts.append("")

    # holding_analysis
    holdings_analysis = report.get("holding_analysis") or []
    if holdings_analysis:
        parts.append("## 🛠️ 单标的诊断与调仓建议")
        parts.append("结合本地 K 线 / RSI / 外部研究做交叉诊断：\n")
        sorted_items = sorted(
            holdings_analysis,
            key=lambda x: _ACTION_PRIORITY.get(x.get("action_type", "watch"), 99),
        )
        for item in sorted_items:
            code = item.get("target_code", "")
            name = item.get("target_name", code)
            action = item.get("action_type", "watch")
            text, emoji = _ACTION_BADGE.get(action, ("🟡 维持观察", "🟡"))

            parts.append(f"### {emoji} {name} ({code}) —— 【{text}】")
            parts.append(f"> **诊断要点**：**{item.get('title', '')}**")
            parts.append(f"> \n> {item.get('reason', '')}")

            tech = technical.get(code, {})
            tech_bits: list[str] = []
            rsi = tech.get("rsi14")
            if rsi is not None:
                tech_bits.append(f"RSI14: {rsi:.1f}")
            dd = tech.get("drawdown_120d")
            if dd is not None:
                tech_bits.append(f"距高点回撤: {dd:.1f}%")
            ma20 = tech.get("ma20")
            holding = next((h for h in holdings if h.get("code") == code), None)
            if holding and ma20 is not None and holding.get("price"):
                trend = "站上20日线" if holding["price"] > ma20 else "跌破20日线"
                tech_bits.append(f"趋势: {trend}")
            if tech_bits:
                parts.append(f"  \n> **本地指标**：`{'  |  '.join(tech_bits)}`")

            if item.get("evidence_refs"):
                parts.append(f"> \n> *证据：{', '.join(item['evidence_refs'])}*")
            parts.append("\n---\n")

    # watch + questions
    watches = report.get("watch_conditions") or []
    questions = report.get("questions") or []
    if watches or questions:
        parts.append("## 🎯 下一步行动")
        if watches:
            parts.append("### 👁️ 关键观察触发")
            parts.append("| 标的 | 指标 | 触发条件 | 说明 |")
            parts.append("| :--- | :--- | :--- | :--- |")
            for w in watches:
                target = w.get("target_code") or "组合整体"
                parts.append(f"| {target} | {w.get('metric', '')} | `{w.get('condition', '')}` | {w.get('reason', '')} |")
            parts.append("")
        if questions:
            parts.append("### ❓ 需人工确认")
            for q in questions:
                reason = q.get("reason") or "基于本期持仓异常"
                parts.append(f"- **{q.get('question', '')}**  \n  *背景：{reason}*")
            parts.append("")

    if report.get("limitations"):
        parts.append("## ⚠️ 数据边界")
        for lim in report["limitations"]:
            parts.append(f"- {lim}")
        parts.append("")

    return "\n".join(parts).strip()
