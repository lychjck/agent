"""错误兜底节点：当上游节点写入 errors 时由此节点产出降级报告"""

from __future__ import annotations

import datetime as dt
from typing import Any

from langchain_core.messages import AIMessage

from stock_agent.state import AgentState


def error_handler_node(state: AgentState) -> dict[str, Any]:
    errors = state.get("errors", []) or []
    today = dt.date.today().strftime("%Y-%m-%d")

    parts = [
        f"# ⚠️ 投资诊断 Agent 异常报告 ({today})",
        "",
        f"运行过程中遇到 {len(errors)} 个节点异常，已停止后续步骤。",
        "",
        "## 错误详情",
    ]
    for i, err in enumerate(errors, start=1):
        parts.append(f"\n### {i}. 节点 `{err.get('node', '?')}` 出错")
        parts.append(f"- 类型：`{err.get('type', '')}`")
        parts.append(f"- 信息：{err.get('message', '')}")
        tb = err.get("traceback")
        if tb:
            parts.append(f"\n```\n{tb}\n```")

    md = "\n".join(parts)
    return {"report": md, "messages": [AIMessage(content=md)]}
