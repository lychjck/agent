"""LLM 报告校验、自愈、兜底（agent 自包含，简化版）

设计原则：
- 只保留 agent 实际需要的能力：schema 校验、evidence_refs 过滤、未知标的剔除、
  缺失标的兜底补齐、与 holding 相关的 contradiction 兜底。
- 不再依赖主项目 stock_assistant 的任何模块。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from stock_agent.schema.report import (
    AgentReport,
    VALID_HOLDING_ACTION_TYPES,
    VALID_SEVERITIES,
)


# ---------- JSON 文本预处理 ----------

def strip_json_markdown(text: str) -> str:
    clean = (text or "").strip()
    if clean.startswith("```json"):
        clean = clean[7:].strip()
    elif clean.startswith("```"):
        clean = clean[3:].strip()
    if clean.endswith("```"):
        clean = clean[:-3].strip()
    return clean


def load_agent_report_json(text: str) -> dict[str, Any]:
    clean = strip_json_markdown(text)
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError:
        # 兜底：尝试只取最外层 {...}
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(clean[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM 输出 JSON 顶层必须是对象")
    return payload


# ---------- Schema hint（喂给 LLM 当输出指引）----------

def agent_report_schema_hint() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "summary": {
            "health_score": "0-100 的整数 或 null",
            "status": "简短状态字符串，如 ok / review / fallback",
            "brief": "一句话总结，必须基于输入数据，不能编造",
        },
        "diagnosis": [
            {
                "id": "diag:unique_id",
                "title": "诊断标题",
                "severity": "low|medium|high|critical",
                "explanation": "解释具体数据事实",
                "evidence_refs": ["必须来自输入 evidence_index 的 key"],
            }
        ],
        "holding_analysis": [
            {
                "target_code": "510300",
                "target_name": "沪深300ETF",
                "action_type": "buy|reduce|hold|watch|rebalance|classify_required",
                "title": "单标的建议标题",
                "reason": "基于该标的的具体解释",
                "evidence_refs": [
                    "holding:510300:technical",
                    "holding:510300:classification",
                ],
            }
        ],
        "watch_conditions": [
            {
                "id": "watch:unique_id",
                "target_code": "可为空",
                "metric": "观察指标",
                "condition": "触发条件",
                "reason": "为什么观察",
                "evidence_refs": ["来自 evidence_index"],
            }
        ],
        "questions": [
            {
                "id": "q:unique_id",
                "question": "需要用户确认的问题",
                "reason": "为什么需要确认",
                "evidence_refs": ["来自 evidence_index"],
            }
        ],
        "limitations": ["数据不足或边界条件"],
    }


# ---------- evidence_refs 标准化与过滤 ----------

def _compact_code(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum())


def _normalize_ref(ref: str, evidence_index: dict[str, Any]) -> str:
    text = str(ref or "").strip().replace("：", ":").replace(" ", "")
    if text in evidence_index:
        return text
    if text.startswith("holding:"):
        parts = text.split(":")
        if len(parts) >= 3:
            code = _compact_code(parts[1])
            kind = parts[2]
            candidate = f"holding:{code}:{kind}"
            if candidate in evidence_index:
                return candidate
    return text


def _filter_refs(refs: list[str], evidence_index: dict[str, Any]) -> list[str]:
    if not isinstance(refs, list):
        return []
    seen: set[str] = set()
    valid: list[str] = []
    for ref in refs:
        key = _normalize_ref(str(ref), evidence_index)
        if key in evidence_index and key not in seen:
            seen.add(key)
            valid.append(key)
    return valid


def _filter_holding_refs(
    refs: list[str],
    evidence_index: dict[str, Any],
    target_code: str,
) -> list[str]:
    """单标的 evidence_refs：对于 holding:* 类引用，只允许引用自己的 code"""
    filtered = _filter_refs(refs, evidence_index)
    if not target_code:
        return filtered
    own_prefix = f"holding:{target_code}:"
    return [
        ref for ref in filtered
        if not ref.startswith("holding:") or ref.startswith(own_prefix)
    ]


# ---------- payload 别名兼容 ----------

def _normalize_payload_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    """LLM 偶尔会写 evidence_ref(单数)，统一成 evidence_refs"""
    normalized = dict(payload)
    for section in ("diagnosis", "holding_analysis", "watch_conditions", "questions"):
        rows = normalized.get(section)
        if not isinstance(rows, list):
            continue
        clean_rows: list[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                clean_rows.append(row)
                continue
            clean_row = dict(row)
            if "evidence_refs" not in clean_row and "evidence_ref" in clean_row:
                clean_row["evidence_refs"] = clean_row.get("evidence_ref")
            clean_rows.append(clean_row)
        normalized[section] = clean_rows
    return normalized


# ---------- holding 行为兜底 ----------

def _deterministic_action_type(holding: dict[str, Any] | None) -> str:
    """无分类信息时，返回 classify_required；其他默认 watch"""
    if not isinstance(holding, dict):
        return "watch"
    cls = holding.get("classification") or {}
    if isinstance(cls, dict):
        ac = cls.get("primary_class") or cls.get("asset_class")
        if not ac or str(ac).lower() == "unknown":
            return "classify_required"
    return "watch"


def _text_contradicts_action(action_type: str, title: str, reason: str) -> bool:
    text = f"{title} {reason}"
    if action_type == "reduce":
        return any(t in text for t in ("建议持有", "继续持有", "可加仓", "建议加仓"))
    if action_type == "buy":
        return any(t in text for t in ("减仓", "暂停加仓", "止损", "赎回"))
    if action_type == "hold":
        return any(t in text for t in ("建议减仓", "止损", "赎回", "建议加仓"))
    if action_type == "classify_required":
        return any(t in text for t in ("建议减仓", "建议加仓", "止损", "赎回"))
    return False


_DEFAULT_TITLE_BY_TYPE = {
    "buy": "候选买入动作待复核",
    "reduce": "候选减仓动作待复核",
    "hold": "继续跟踪持仓",
    "watch": "基于现有数据观察",
    "rebalance": "候选再平衡动作待复核",
    "classify_required": "需要先补充分类信息",
}


def _normalize_holding_copy(item: dict[str, Any], holding: dict[str, Any] | None) -> dict[str, Any]:
    action_type = str(item.get("action_type", "watch"))
    title = str(item.get("title", "") or "")
    reason = str(item.get("reason", "") or "")
    if not _text_contradicts_action(action_type, title, reason):
        return item
    fallback_title = _DEFAULT_TITLE_BY_TYPE.get(action_type, "基于现有数据观察")
    item["title"] = fallback_title
    item["reason"] = reason or "动作类型已按结构化校验修正。"
    return item


def fallback_holding_analysis_from_context(
    holdings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """没有 LLM 建议时，根据每个 holding 自动产出一条 watch 项作为兜底"""
    out: list[dict[str, Any]] = []
    for h in holdings:
        code = str(h.get("code", ""))
        name = str(h.get("name", ""))
        if not code:
            continue
        action_type = _deterministic_action_type(h)
        tech = h.get("technical") or {}

        reason_parts = []
        if isinstance(tech, dict):
            rsi = tech.get("rsi14")
            dd = tech.get("drawdown_120d")
            if rsi is not None:
                reason_parts.append(f"RSI14={rsi:.1f}")
            if dd is not None:
                reason_parts.append(f"120日回撤={dd:.1f}%")
        if h.get("profit_rate") is not None:
            reason_parts.append(f"盈亏={h['profit_rate']:+.1f}%")
        reason = "；".join(reason_parts) if reason_parts else "数据不足，建议继续观察"

        evidence_refs = []
        if isinstance(tech, dict) and tech:
            evidence_refs.append(f"holding:{code}:technical")
        if h.get("classification"):
            evidence_refs.append(f"holding:{code}:classification")

        out.append({
            "target_code": code,
            "target_name": name,
            "action_type": action_type,
            "title": _DEFAULT_TITLE_BY_TYPE.get(action_type, "基于现有数据观察"),
            "reason": reason,
            "evidence_refs": evidence_refs,
        })
    return out


# ---------- 主入口 ----------

def validate_agent_report(
    payload: dict[str, Any],
    *,
    evidence_index: dict[str, Any],
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    校验 LLM 输出报告并自愈。失败时抛 ValueError（让上层走兜底报告）。

    自愈点：
    - severity / action_type 合法化
    - 未知 holding 剔除并转成 question
    - evidence_refs 过滤、单标的不能引用别人的证据
    - 缺失的 holding 用 fallback_holding_analysis_from_context 补齐
    - LLM 文本与 action_type 矛盾时统一改写
    """
    payload = _normalize_payload_aliases(payload)
    try:
        report_model = AgentReport.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"LLM 报告 schema 校验失败: {exc}") from exc

    report = report_model.model_dump()
    questions: list[dict[str, Any]] = list(report.get("questions", []))

    # diagnosis
    for index, item in enumerate(report["diagnosis"], start=1):
        if not item.get("id"):
            item["id"] = f"diagnosis:{index}"
        if item["severity"] not in VALID_SEVERITIES:
            item["severity"] = "medium"
        item["evidence_refs"] = _filter_refs(item.get("evidence_refs", []), evidence_index)

    # holding_analysis
    holding_by_code = {str(h.get("code", "")): h for h in (holdings or []) if h.get("code")}
    valid_analysis: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for item in report["holding_analysis"]:
        code = str(item.get("target_code", ""))
        if code and holding_by_code and code not in holding_by_code:
            questions.append({
                "id": f"q:unknown_holding:{code}",
                "question": f"LLM 提到了未知持仓 {code}，需要核对。",
                "reason": item.get("reason", "未知标的不允许进入单标的建议"),
                "evidence_refs": _filter_refs(item.get("evidence_refs", []), evidence_index),
            })
            continue
        if item.get("action_type") not in VALID_HOLDING_ACTION_TYPES:
            item["action_type"] = "watch"
        # classify_required 的强约束
        det = _deterministic_action_type(holding_by_code.get(code))
        if det == "classify_required":
            item["action_type"] = "classify_required"
        if not item.get("target_name") and code in holding_by_code:
            item["target_name"] = str(holding_by_code[code].get("name", ""))
        if not item.get("title"):
            item["title"] = _DEFAULT_TITLE_BY_TYPE.get(item["action_type"], "基于现有数据观察")
        item = _normalize_holding_copy(item, holding_by_code.get(code))
        item["evidence_refs"] = _filter_holding_refs(
            item.get("evidence_refs", []), evidence_index, code
        )
        if code:
            seen_codes.add(code)
        valid_analysis.append(item)

    # 用 fallback 补齐没出现在 LLM 输出里的 holdings
    for fb in fallback_holding_analysis_from_context(holdings or []):
        code = str(fb.get("target_code", ""))
        if code and code not in seen_codes:
            fb["evidence_refs"] = _filter_refs(fb.get("evidence_refs", []), evidence_index)
            valid_analysis.append(fb)
    report["holding_analysis"] = valid_analysis

    # watch_conditions
    for index, item in enumerate(report["watch_conditions"], start=1):
        if not item.get("id"):
            item["id"] = f"watch:{index}"
        item["evidence_refs"] = _filter_refs(item.get("evidence_refs", []), evidence_index)

    # questions
    for index, item in enumerate(questions, start=1):
        if not item.get("id"):
            item["id"] = f"q:{index}"
        item["evidence_refs"] = _filter_refs(item.get("evidence_refs", []), evidence_index)
    report["questions"] = questions

    # 收集所有引用过的 evidence
    used: set[str] = set()
    for section in ("diagnosis", "holding_analysis", "watch_conditions", "questions"):
        for item in report.get(section, []):
            used.update(item.get("evidence_refs", []))
    report["evidence"] = sorted(used)

    return report
