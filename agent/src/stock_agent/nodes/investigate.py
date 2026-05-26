"""探案节点 - 对异常标的进行深入调研"""

from typing import Any

from langchain_core.messages import HumanMessage

from stock_agent.state import AgentState
from stock_agent.mcp_client import McpClient


def investigate_node(state: AgentState, *, mcp: McpClient) -> dict[str, Any]:
    """
    探案节点：
    1. 对异常标的查询 ETF 重仓股
    2. 搜索相关财经新闻
    3. 汇总调研结果
    """
    anomalies = state.get("anomalies", [])
    if not anomalies:
        return {"phase": "report", "investigations": []}

    investigations = []

    # 限制调研数量，避免过多 API 调用
    top_anomalies = sorted(
        anomalies,
        key=lambda x: abs(x.get("profit_rate", 0)),
        reverse=True,
    )[:5]

    for anomaly in top_anomalies:
        code = anomaly["code"]
        name = anomaly["name"]
        investigation: dict[str, Any] = {
            "code": code,
            "name": name,
            "anomaly": anomaly,
            "constituents": None,
            "news": None,
        }

        # 1. 查询 ETF 重仓股（仅对 ETF 类标的）
        if len(code) == 6 and code[0] in ("1", "5"):  # 场内 ETF 代码特征
            try:
                constituents = mcp.get_etf_constituents([code])
                if constituents.get("ok"):
                    etf_data = constituents.get("results", {}).get(code, {})
                    if etf_data.get("ok"):
                        top_stocks = etf_data.get("constituents", [])[:5]
                        investigation["constituents"] = top_stocks
            except Exception:
                pass

        # 2. 搜索相关新闻
        search_query = f"{name} 最新消息 行情分析"
        try:
            news = mcp.web_search(search_query, max_results=3)
            if news.get("ok"):
                investigation["news"] = news.get("results", [])
        except Exception:
            pass

        investigations.append(investigation)

    summary = f"完成 {len(investigations)} 只异常标的的深入调研"
    return {
        "investigations": investigations,
        "messages": [HumanMessage(content=summary)],
        "phase": "report",
    }
