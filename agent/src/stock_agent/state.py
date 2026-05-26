"""Agent 状态定义"""

from typing import Annotated, Any, Literal
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """LangGraph Agent 的全局状态"""
    
    # 消息历史（LangGraph 自动追加）
    messages: Annotated[list[BaseMessage], add_messages]
    
    # 持仓快照数据
    holdings: list[dict[str, Any]]
    portfolio_profile: dict[str, Any]
    classifications: dict[str, Any]
    
    # 异常标的列表（Z-Score > 2 或亏损超阈值）
    anomalies: list[dict[str, Any]]
    
    # 探案调研结果
    investigations: list[dict[str, Any]]
    
    # 最终诊断报告
    report: str
    
    # 流程控制
    phase: Literal["init", "diagnose", "investigate", "report", "done"]
