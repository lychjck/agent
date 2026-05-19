from typing import Any

from stock_assistant.agents.agent_tools import is_etf_like_holding
from stock_assistant.agents.agent_workspace import AgentWorkspace


def goal_requires_full_technical_coverage(goal: str) -> bool:
    normalized = goal.strip()
    return any(token in normalized for token in ("每个", "全部", "所有", "逐个"))


def missing_technical_codes(workspace: AgentWorkspace, goal: str) -> list[str]:
    if not goal_requires_full_technical_coverage(goal):
        return []
    existing = {
        str((item.get("holding") or {}).get("code", ""))
        for item in workspace.technical_results
        if isinstance(item.get("holding"), dict)
    }
    return [
        holding.code
        for holding in workspace.ensure_holdings()
        if holding.code and holding.code not in existing
    ]


def build_coverage_prompt(missing_codes: list[str]) -> str:
    return (
        "最终报告暂缓：用户目标要求逐个覆盖当前持仓，但仍有标的缺少 technical observation。"
        f"后端已补充请求缺失标的技术指标，缺失数量={len(missing_codes)}。"
        "收到这些 observation 后必须重新做 observation_reflection；只有覆盖缺口清零，"
        "或 observation 明确说明某个标的不可分析，才可以 final_report。"
    )


def goal_requires_external_research(goal: str) -> bool:
    normalized = goal.strip()
    return any(token in normalized for token in ("持仓", "ETF", "基金", "股票", "市场", "行业", "宏观", "行情"))


def important_holding_records(workspace: AgentWorkspace, *, min_weight_pct: float = 1.0) -> list[dict[str, Any]]:
    total_value = workspace.total_value()
    if not total_value:
        return []
    records: list[dict[str, Any]] = []
    for holding in workspace.ensure_holdings():
        if holding.market_value is None:
            continue
        weight_pct = holding.market_value / total_value * 100
        if weight_pct >= min_weight_pct:
            records.append({
                "code": holding.code,
                "name": holding.name,
                "weight_pct": round(weight_pct, 4),
            })
    return sorted(records, key=lambda item: float(item.get("weight_pct") or 0), reverse=True)


def external_research_gap(
    workspace: AgentWorkspace,
    goal: str,
    registry: dict[str, Any],
    web_search_queries: list[str],
    web_read_count: int,
) -> dict[str, Any] | None:
    if "web_search" not in registry and "opencli_command" not in registry:
        return None
    if "web_read" not in registry and "opencli_command" not in registry:
        return None
    if not goal_requires_external_research(goal):
        return None
    important = important_holding_records(workspace)
    query_blob = "\n".join(web_search_queries).lower()
    missing = [
        item for item in important
        if str(item.get("code", "")).lower() not in query_blob
        and str(item.get("name", "")).lower() not in query_blob
    ]
    reasons: list[str] = []
    if not web_search_queries:
        reasons.append("尚未执行 opencli_command/web_search")
    if web_read_count <= 0:
        reasons.append("尚未执行 web_read/opencli web read，只有搜索结果摘要，没有打开来源页")
    if missing:
        reasons.append(f"权重>=1%的标的仍有 {len(missing)} 个未在搜索 query 中逐项覆盖")
    if not reasons:
        return None
    return {
        "reasons": reasons,
        "important_count": len(important),
        "searched_queries": web_search_queries,
        "web_read_count": web_read_count,
        "missing_holding_research": missing,
    }


def build_external_research_gate_prompt(gap: dict[str, Any]) -> str:
    missing = gap.get("missing_holding_research") or []
    missing_text = ", ".join(
        f"{item.get('code')} {item.get('name')}({item.get('weight_pct')}%)"
        for item in missing[:12]
    )
    return (
        "最终报告暂缓：后端检查发现外部研究覆盖不足，不能把未完成的搜索说成已经覆盖。"
        f"原因：{'; '.join(str(item) for item in gap.get('reasons', []))}。"
        f"尚未逐项搜索的核心标的：{missing_text or '无'}。"
        "下一步必须继续调用工具："
        "1) 如果 web_read_count=0，先从已有 opencli_command/web_search 结果中选择最相关 URL 调用 web_read 或 opencli_command(site='web', command='read')；"
        "2) 对 missing_holding_research 中的标的分批调用 opencli_command 或 web_search，每次最多 6 个工具调用，query/positionals 必须包含标的代码和名称；"
        "3) 之后重新 observation_reflection，coverage_notes 必须基于实际工具调用，不得虚报。"
    )


def final_report_missing_holding_analysis(
    workspace: AgentWorkspace,
    goal: str,
    report_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not goal_requires_full_technical_coverage(goal):
        return []
    report = report_payload or {}
    if isinstance(report.get("report"), dict):
        report = report["report"]
    items = report.get("holding_analysis")
    if not isinstance(items, list):
        items = []
    covered = {
        str(item.get("target_code") or item.get("code") or "").strip()
        for item in items
        if isinstance(item, dict)
    }
    target_holdings = [
        holding for holding in workspace.ensure_holdings()
        if holding.code
        and (
            ("ETF" not in goal and "etf" not in goal.lower())
            or is_etf_like_holding(holding.code, holding.name, holding.asset_type)
        )
    ]
    return [
        {"code": holding.code, "name": holding.name, "asset_type": holding.asset_type}
        for holding in target_holdings
        if holding.code not in covered
    ]


def build_holding_analysis_gate_prompt(missing: list[dict[str, Any]]) -> str:
    missing_text = ", ".join(f"{item.get('code')} {item.get('name')}" for item in missing[:20])
    return (
        "最终报告暂缓：已收集到的证据没有丢失；问题是 final_report.holding_analysis 没有逐项写入每个 ETF 的建议。"
        f"缺少 {len(missing)} 个标的：{missing_text}。"
        "下一步不要重新做无关总结；请基于已经获得的本地技术、分类、组合画像和外部搜索证据，"
        "只输出合法 JSON，type 必须是 final_report；report 可以只包含 holding_analysis、limitations、evidence，"
        "holding_analysis 只写这些缺失标的，后端会与上一版 final_report 合并。不要输出 final_report_patch。"
        "如果某个标的缺少外部证据，action_type 只能是 hold/watch，并在 reason 与 limitations 中说明证据不足。"
    )
