"""LangGraph Agent 状态定义"""

from operator import add
from typing import Annotated, Any
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    """全局状态。total=False 让所有键都是可选，避免初始化时必须铺全字段"""

    # 消息历史（LangGraph 自动追加）
    messages: Annotated[list[BaseMessage], add_messages]

    # 持仓快照
    holdings: list[dict[str, Any]]
    portfolio_profile: dict[str, Any]
    classifications: dict[str, Any]
    technical_data: dict[str, Any]
    anomalies: list[dict[str, Any]]

    # 并发研究：被 Send 出的子节点会写到 investigations，自带 add reducer 合并
    investigations: Annotated[list[dict[str, Any]], add]

    # 报告
    report_data: dict[str, Any]
    report: str

    # 错误兜底：任何节点失败可以写到 errors，error_handler 节点用它生成兜底报告
    errors: Annotated[list[dict[str, Any]], add]
