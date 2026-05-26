"""报告生成节点 - 调用 LLM 生成最终诊断报告"""

import json
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from stock_agent.state import AgentState


REPORT_SYSTEM_PROMPT = """你是一位专业的投资组合诊断分析师。请根据以下持仓数据、异常信号和调研结果，生成一份简洁的投资诊断报告。

报告要求：
1. 组合概览：总市值、资产配置比例、核心持仓
2. 风险提示：列出所有异常信号及其严重程度
3. 调研发现：对异常标的的根因分析（如有调研数据）
4. 操作建议：基于当前市场状况的具体建议（减仓/加仓/持有/观望）

注意：
- 使用中文
- 数据要精确，不要编造
- 建议要具体可执行
- 区分紧急操作和观察等待
"""


def report_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """
    报告生成节点：
    汇总所有数据，调用 LLM 生成结构化诊断报告
    """
    holdings = state.get("holdings", [])
    profile = state.get("portfolio_profile", {})
    anomalies = state.get("anomalies", [])
    investigations = state.get("investigations", [])

    # 构建 LLM 输入
    context_parts = []

    # 组合概览
    context_parts.append("## 组合概览")
    context_parts.append(f"总市值: ¥{profile.get('total_value', 0):,.0f}")
    
    by_class = profile.get("by_asset_class", {})
    if by_class:
        context_parts.append("资产配置:")
        for cls, pct in sorted(by_class.items(), key=lambda x: -x[1]):
            context_parts.append(f"  - {cls}: {pct*100:.1f}%")

    # 持仓明细（前 10 大）
    context_parts.append("\n## 前10大持仓")
    sorted_holdings = sorted(holdings, key=lambda x: -x.get("value", 0))[:10]
    for h in sorted_holdings:
        context_parts.append(
            f"  - {h.get('name', h['code'])} ({h['code']}): "
            f"市值¥{h.get('value', 0):,.0f}, "
            f"盈亏{h.get('profit_rate', 0):+.1f}%, "
            f"持有{h.get('hold_days', 0):.0f}天"
        )

    # 异常信号
    if anomalies:
        context_parts.append(f"\n## 异常信号 ({len(anomalies)}个)")
        for a in anomalies:
            context_parts.append(f"  - [{a['type']}] {a['name']}: {a['reason']}")

    # 调研结果
    if investigations:
        context_parts.append(f"\n## 调研结果 ({len(investigations)}只标的)")
        for inv in investigations:
            context_parts.append(f"\n### {inv['name']} ({inv['code']})")
            context_parts.append(f"异常原因: {inv['anomaly']['reason']}")
            
            if inv.get("constituents"):
                context_parts.append("重仓股:")
                for stock in inv["constituents"][:3]:
                    context_parts.append(f"  - {stock.get('name', '')} ({stock.get('code', '')}): {stock.get('weight', '')}%")
            
            if inv.get("news"):
                context_parts.append("相关新闻:")
                for news in inv["news"][:2]:
                    context_parts.append(f"  - {news.get('title', '无标题')}")

    # 组合风险提示
    observations = profile.get("observations", [])
    if observations:
        context_parts.append("\n## 系统风险提示")
        for obs in observations:
            context_parts.append(f"  - {obs}")

    context = "\n".join(context_parts)

    # 调用 LLM 生成报告
    messages = [
        SystemMessage(content=REPORT_SYSTEM_PROMPT),
        HumanMessage(content=f"请基于以下数据生成投资诊断报告：\n\n{context}"),
    ]

    response = llm.invoke(messages)
    report_text = response.content if hasattr(response, "content") else str(response)

    return {
        "report": report_text,
        "messages": [AIMessage(content=report_text)],
        "phase": "done",
    }
