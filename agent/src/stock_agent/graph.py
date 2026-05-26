"""LangGraph 状态图构建"""

from functools import partial
from typing import Any, Literal

from langgraph.graph import StateGraph, END

from stock_agent.state import AgentState
from stock_agent.mcp_client import McpClient
from stock_agent.nodes.diagnose import diagnose_node
from stock_agent.nodes.investigate import investigate_node
from stock_agent.nodes.report import report_node


def route_after_diagnose(state: AgentState) -> Literal["investigate", "report"]:
    """诊断后路由：有异常则探案，无异常直接出报告"""
    if state.get("anomalies"):
        return "investigate"
    return "report"


def build_graph(mcp: McpClient, llm: Any) -> StateGraph:
    """
    构建投资诊断 Agent 的状态图
    
    流程:
        START → diagnose → [有异常?] → investigate → report → END
                                    → report → END
    """
    graph = StateGraph(AgentState)

    # 注册节点（通过 partial 注入依赖）
    graph.add_node("diagnose", partial(diagnose_node, mcp=mcp))
    graph.add_node("investigate", partial(investigate_node, mcp=mcp))
    graph.add_node("report", partial(report_node, llm=llm))

    # 设置入口
    graph.set_entry_point("diagnose")

    # 条件路由：诊断后根据是否有异常决定下一步
    graph.add_conditional_edges(
        "diagnose",
        route_after_diagnose,
        {
            "investigate": "investigate",
            "report": "report",
        },
    )

    # 探案完成后进入报告
    graph.add_edge("investigate", "report")

    # 报告完成后结束
    graph.add_edge("report", END)

    return graph


def compile_graph(mcp: McpClient, llm: Any):
    """编译状态图为可执行的 Runnable"""
    graph = build_graph(mcp, llm)
    return graph.compile()
