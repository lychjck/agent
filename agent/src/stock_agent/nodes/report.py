"""报告生成节点：调用 LLM 输出结构化 JSON 报告"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from stock_agent.nodes._safe import safe_node
from stock_agent.schema import agent_report_schema_hint, load_agent_report_json
from stock_agent.state import AgentState


# LLM 交互记录保存目录
_TRACES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "traces"


def _save_llm_trace(
    messages: list[BaseMessage],
    response_text: str,
    error: str | None,
    model_name: str = "",
) -> None:
    """把发给 LLM 的完整 messages 和返回内容保存到 traces/ 目录"""
    try:
        _TRACES_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        trace_file = _TRACES_DIR / f"{ts}-llm-chat.md"

        parts: list[str] = [
            f"# LLM 交互记录 ({ts})",
            "",
            f"**模型**: `{model_name or 'unknown'}`",
            "",
        ]

        for msg in messages:
            role = msg.__class__.__name__.replace("Message", "").upper()
            content = msg.content if hasattr(msg, "content") else str(msg)
            parts.append(f"## {role}")
            parts.append("")
            parts.append(content)
            parts.append("")
            parts.append("---")
            parts.append("")

        parts.append("## LLM RESPONSE")
        parts.append("")
        if error:
            parts.append(f"**ERROR**: {error}")
            parts.append("")
        if response_text:
            parts.append("```json")
            parts.append(response_text)
            parts.append("```")
        else:
            parts.append("*(empty)*")

        trace_file.write_text("\n".join(parts), encoding="utf-8")
    except Exception:
        pass  # 保存失败不影响主流程


REPORT_SYSTEM_PROMPT = """你是严格基于事实证据的中文持仓诊断模型。
基于用户提供的数据生成结构化诊断报告。只能解释给定的持仓 / 技术指标 / 外部研究事实，
严禁编造未给出的新闻、宏观政策或未来收益。

输出硬性要求：
1. 你的所有回复必须是一个严格、合法的 JSON 对象。绝不能包含 ```json 或 ``` 等 Markdown 标记，
   不能在 JSON 之前/之后添加任何解释性文字。
2. 必须为 holdings 列表中【每一个】标的，在 holding_analysis 中产出一条记录。
   字段包含 target_code、target_name、action_type（buy|reduce|hold|watch|rebalance|classify_required）、
   title、reason、evidence_refs（仅可引用形如 holding:{code}:technical 这类输入提供的 key）。
3. 数据缺失时优先使用 watch + 在 limitations 中说明，不要编造。

必须严格符合给定的输出 Schema。
"""


def _build_context(state: AgentState) -> str:
    holdings = state.get("holdings", [])
    profile = state.get("portfolio_profile", {})
    technical = state.get("technical_data", {})
    anomalies = state.get("anomalies", [])
    investigations = state.get("investigations", [])

    parts: list[str] = []

    parts.append("## 组合概览")
    parts.append(f"总市值: ¥{profile.get('total_value', 0):,.0f}")
    by_class = profile.get("by_asset_class", {})
    if by_class:
        parts.append("资产配置：")
        for cls, pct in sorted(by_class.items(), key=lambda x: -x[1]):
            parts.append(f"  - {cls}: {pct * 100:.1f}%")

    parts.append("\n## 持仓与技术面事实")
    for h in holdings:
        code = h["code"]
        name = h.get("name", code)
        val = h.get("value", 0) or 0
        pr = h.get("profit_rate", 0) or 0
        weight = h.get("weight_pct", 0) or 0
        tech = technical.get(code, {})
        rsi = tech.get("rsi14", "N/A")
        dd = tech.get("drawdown_120d", "N/A")
        ma20 = tech.get("ma20", "N/A")
        ma60 = tech.get("ma60", "N/A")
        parts.append(
            f"  - {name} ({code}): 权重={weight:.2f}%, 市值=¥{val:,.0f}, 盈亏={pr:+.1f}%, "
            f"RSI={rsi}, 120日回撤={dd}, MA20={ma20}, MA60={ma60}"
        )

    if anomalies:
        parts.append("\n## 系统识别的异常信号")
        for a in anomalies:
            parts.append(f"  - [{a['type']}] {a.get('name', a['code'])}: {a['reason']}")

    if investigations:
        parts.append("\n## 外部研究事实")
        for inv in investigations:
            if inv["type"] == "holding_research":
                parts.append(f"\n### {inv['name']} ({inv['code']}) [权重 {inv['weight_pct']}%]")
                if inv.get("constituents"):
                    parts.append("  重仓股：")
                    for s in inv["constituents"]:
                        parts.append(f"    - {s.get('name', '')} ({s.get('code', '')}): {s.get('weight', '')}%")
                if inv.get("news"):
                    parts.append("  相关新闻摘要：")
                    for n in inv["news"][:3]:
                        snippet = (n.get("snippet") or "")[:200]
                        parts.append(f"    - {n.get('title', '')} | {snippet}")
            elif inv["type"] == "theme_research":
                parts.append(f"\n### 主题：{inv['theme']}")
                if inv.get("news"):
                    for n in inv["news"][:3]:
                        snippet = (n.get("snippet") or "")[:200]
                        parts.append(f"    - {n.get('title', '')} | {snippet}")

    if profile.get("observations"):
        parts.append("\n## 内置组合级风险")
        for obs in profile["observations"]:
            parts.append(f"  - {obs}")

    return "\n".join(parts)


def _fallback_report(error: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "summary": {
            "health_score": 60,
            "status": "fallback",
            "brief": "LLM 报告生成失败，已触发本地兜底逻辑。",
        },
        "diagnosis": [],
        "holding_analysis": [],
        "watch_conditions": [],
        "questions": [{
            "id": "q:llm_failed",
            "question": "大模型诊断报告未能生成，请复核本地兜底建议是否合理。",
            "reason": f"原始错误：{error}",
        }],
        "limitations": ["由于 LLM 调用或解析失败，单标的建议改由本地规则补齐。"],
    }


@safe_node("report")
def report_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    context = _build_context(state)
    schema_hint = json.dumps(agent_report_schema_hint(), ensure_ascii=False, indent=2)

    user_prompt = (
        f"请基于以下事实生成结构化诊断报告：\n\n{context}\n\n"
        f"输出 Schema:\n{schema_hint}"
    )
    messages = [
        SystemMessage(content=REPORT_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    text = ""
    err: str | None = None
    try:
        resp = llm.invoke(messages)
        text = resp.content if hasattr(resp, "content") else str(resp)
    except Exception as exc:  # noqa: BLE001
        err = f"llm.invoke 异常: {exc}"

    # 保存 LLM 交互记录供调试
    model_name = getattr(llm, "model_name", "") or getattr(llm, "model", "") or ""
    _save_llm_trace(messages, text, err, model_name=model_name)

    if err is None:
        try:
            return {"report_data": load_agent_report_json(text)}
        except Exception as exc:  # noqa: BLE001
            err = f"json 解析失败: {exc}"

    return {"report_data": _fallback_report(err)}
