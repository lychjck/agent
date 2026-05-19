import datetime as dt
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stock_assistant.core.llm import call_llm, llm_enabled
from stock_assistant.core.models import CandidateAction, Holding, InstrumentClassification, RiskFlag, candidate_action_to_dict
from stock_assistant.core.utils import config_bool, get_attr, log


VALID_SEVERITIES = {"low", "medium", "high", "critical"}
VALID_ACTION_STANCES = {"support", "defer", "reject", "need_user_rule"}
VALID_HOLDING_ACTION_TYPES = {"buy", "reduce", "hold", "watch", "rebalance", "classify_required"}


class ReportSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    health_score: int | None = Field(default=None, ge=0, le=100)
    status: str = "unknown"
    brief: str = ""


class DiagnosisItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    title: str = ""
    severity: str = "medium"
    explanation: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class ActionReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_action_id: str = ""
    stance: str = "need_user_rule"
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class HoldingAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_code: str = ""
    target_name: str = ""
    action_type: str = "watch"
    title: str = ""
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class WatchCondition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    target_code: str = ""
    metric: str = ""
    condition: str = ""
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class QuestionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    question: str = ""
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    summary: ReportSummary = Field(default_factory=ReportSummary)
    diagnosis: list[DiagnosisItem] = Field(default_factory=list)
    holding_analysis: list[HoldingAnalysis] = Field(default_factory=list)
    action_reviews: list[ActionReview] = Field(default_factory=list)
    watch_conditions: list[WatchCondition] = Field(default_factory=list)
    questions: list[QuestionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


def strip_json_markdown(text: str) -> str:
    clean = text.strip()
    if clean.startswith("```json"):
        clean = clean[7:].strip()
    elif clean.startswith("```"):
        clean = clean[3:].strip()
    if clean.endswith("```"):
        clean = clean[:-3].strip()
    return clean


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def rounded_money(value: Any) -> float | None:
    number = safe_float(value)
    if number is None:
        return None
    return round(number, 2)


def sanitize_text(value: Any, max_chars: int = 600) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...[truncated]"


def compact_pct_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, float] = {}
    for key, pct in value.items():
        number = safe_float(pct)
        if number is not None:
            output[str(key)] = round(number, 2)
    return dict(sorted(output.items(), key=lambda item: item[1], reverse=True))


def compact_position(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(position.get("code", "")),
        "name": str(position.get("name", "")),
        "market_value": rounded_money(position.get("market_value")),
        "weight_pct": safe_float(position.get("weight")),
        "asset_class": str(position.get("asset_class", "unknown")),
        "sector": str(position.get("sector", "unknown")),
        "theme": str(position.get("theme", "")),
        "strategy": str(position.get("strategy", "unknown")),
        "region": str(position.get("region", "unknown")),
        "asset_type": str(position.get("asset_type", "unknown")),
        "classification_confidence": safe_float(position.get("classification_confidence")),
        "reviewed_by_user": bool(position.get("reviewed_by_user", False)),
    }


def compact_portfolio_summary(summary: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    max_positions = int(config.get("llm", {}).get("max_context_positions", 50))
    positions = [compact_position(item) for item in summary.get("positions", [])[:max_positions]]
    return {
        "total_value": rounded_money(summary.get("total_value")),
        "position_count": int(summary.get("position_count", len(positions)) or 0),
        "by_asset_class": compact_pct_map(summary.get("by_asset_class", {})),
        "by_sector": compact_pct_map(summary.get("by_sector", {})),
        "by_theme": compact_pct_map(summary.get("by_theme", {})),
        "by_strategy": compact_pct_map(summary.get("by_strategy", {})),
        "by_region": compact_pct_map(summary.get("by_region", {})),
        "by_asset_type": compact_pct_map(summary.get("by_asset_type", {})),
        "top_positions": [compact_position(item) for item in summary.get("top_positions", [])],
        "positions": positions,
        "unknown_classification_pct": safe_float(summary.get("unknown_classification_pct")) or 0.0,
        "low_confidence_classification_pct": (
            safe_float(summary.get("low_confidence_classification_pct")) or 0.0
        ),
    }


def classification_record(classification: InstrumentClassification | dict[str, Any] | None) -> dict[str, Any]:
    if classification is None:
        return {
            "asset_class": "unknown",
            "sector": "unknown",
            "theme": "",
            "region": "unknown",
            "strategy": "unknown",
            "tracked_index": "",
            "issuer": "",
            "confidence": 0.0,
            "source": "unknown",
            "reviewed_by_user": False,
        }
    return {
        "asset_class": str(get_attr(classification, "asset_class", "unknown") or "unknown"),
        "sector": str(get_attr(classification, "sector", "") or "unknown"),
        "theme": str(get_attr(classification, "theme", "") or ""),
        "region": str(get_attr(classification, "region", "unknown") or "unknown"),
        "strategy": str(get_attr(classification, "strategy", "unknown") or "unknown"),
        "tracked_index": str(get_attr(classification, "tracked_index", "") or ""),
        "issuer": str(get_attr(classification, "issuer", "") or ""),
        "confidence": safe_float(get_attr(classification, "confidence", 0.0)) or 0.0,
        "source": str(get_attr(classification, "source", "unknown") or "unknown"),
        "reviewed_by_user": bool(get_attr(classification, "reviewed_by_user", False)),
    }


def sanitize_classification_evidence(
    classification: InstrumentClassification | dict[str, Any] | None,
) -> list[dict[str, str]]:
    evidence_items = get_attr(classification, "evidence", ()) if classification is not None else ()
    output: list[dict[str, str]] = []
    for item in list(evidence_items)[:3]:
        record = {
            "title": sanitize_text(get_attr(item, "title", ""), 160),
            "url": sanitize_text(get_attr(item, "url", ""), 240),
            "snippet": sanitize_text(get_attr(item, "snippet", ""), 500),
            "published_date": sanitize_text(get_attr(item, "published_date", ""), 60),
            "retrieved_at": sanitize_text(get_attr(item, "retrieved_at", ""), 60),
            "source": sanitize_text(get_attr(item, "source", ""), 80),
            "source_tier": sanitize_text(get_attr(item, "source_tier", ""), 40),
        }
        output.append({key: value for key, value in record.items() if value})
    return output


def holding_record(
    holding: Holding | dict[str, Any],
    classification: InstrumentClassification | dict[str, Any] | None,
    technical: dict[str, Any] | None,
) -> dict[str, Any]:
    code = str(get_attr(holding, "code", ""))
    classification_ref = f"holding:{code}:classification"
    technical_ref = f"holding:{code}:technical"
    return {
        "code": code,
        "name": str(get_attr(holding, "name", "")),
        "asset_type": str(get_attr(holding, "asset_type", "unknown")),
        "quantity": safe_float(get_attr(holding, "quantity")),
        "cost_price": safe_float(get_attr(holding, "cost_price")),
        "market_value": rounded_money(get_attr(holding, "market_value")),
        "profit_pct": safe_float(get_attr(holding, "profit_pct")),
        "hold_profit": rounded_money(get_attr(holding, "hold_profit")),
        "day_profit": rounded_money(get_attr(holding, "day_profit")),
        "classification": classification_record(classification),
        "technical": technical or {},
        "evidence_refs": [classification_ref, technical_ref],
    }


def technical_record(item: dict[str, Any]) -> dict[str, Any]:
    latest = item.get("latest")
    return {
        "ok": bool(item.get("ok", False)),
        "latest_date": str(get_attr(latest, "date", "")) if latest else "",
        "latest_close": safe_float(get_attr(latest, "close")) if latest else None,
        "daily_pct_change": safe_float(get_attr(latest, "pct_change")) if latest else None,
        "ma20": safe_float(item.get("ma20")),
        "ma60": safe_float(item.get("ma60")),
        "ma120": safe_float(item.get("ma120")),
        "ret5_pct": safe_float(item.get("ret5")),
        "ret20_pct": safe_float(item.get("ret20")),
        "rsi14": safe_float(item.get("rsi14")),
        "drawdown_from_120d_high_pct": safe_float(item.get("drawdown")),
        "volatility20_pct": safe_float(item.get("vol20")),
        "volume_ratio": safe_float(item.get("vol_ratio")),
        "profit_pct": safe_float(item.get("profit_pct")),
        "portfolio_weight_pct": safe_float(item.get("weight")),
        "technical_observations": sanitize_text(item.get("reason", ""), 500),
    }


def risk_flag_record(flag: RiskFlag | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(get_attr(flag, "id", "")),
        "code": str(get_attr(flag, "code", "")),
        "label": str(get_attr(flag, "label", "")),
        "severity": str(get_attr(flag, "severity", "medium")),
        "evidence": [sanitize_text(item, 240) for item in list(get_attr(flag, "evidence", ()) or [])],
    }


def action_record(action: CandidateAction | dict[str, Any]) -> dict[str, Any]:
    if isinstance(action, CandidateAction):
        return candidate_action_to_dict(action)
    record = dict(action)
    record.setdefault("evidence", [])
    return record


def build_agent_llm_context(
    *,
    holdings: list[Holding | dict[str, Any]],
    classifications: dict[str, InstrumentClassification | dict[str, Any]],
    technical_results: list[dict[str, Any]],
    portfolio_summary: dict[str, Any],
    observations: list[dict[str, Any]],
    risk_flags: list[RiskFlag | dict[str, Any]],
    candidate_actions: list[CandidateAction | dict[str, Any]],
    history_diff: dict[str, Any] | None,
    ledger_summary: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    evidence_index: dict[str, Any] = {}
    technical_by_code = {
        str(get_attr(item.get("holding"), "code", "")): item
        for item in technical_results
        if item.get("holding") is not None
    }

    holding_payload: list[dict[str, Any]] = []
    for holding in holdings:
        code = str(get_attr(holding, "code", ""))
        classification = classifications.get(code)
        technical = technical_record(technical_by_code.get(code, {}))
        holding_payload.append(holding_record(holding, classification, technical))

        evidence_index[f"holding:{code}:classification"] = {
            "type": "classification",
            "code": code,
            "title": f"{get_attr(holding, 'name', code)} 分类",
            "facts": classification_record(classification),
            "source_evidence": sanitize_classification_evidence(classification),
        }
        evidence_index[f"holding:{code}:technical"] = {
            "type": "technical",
            "code": code,
            "title": f"{get_attr(holding, 'name', code)} 技术指标",
            "facts": technical,
        }

    clean_observations: list[dict[str, Any]] = []
    for observation in observations:
        obs = dict(observation)
        obs_id = str(obs.get("id", ""))
        if obs_id:
            evidence_index[obs_id] = {
                "type": "observation",
                "title": sanitize_text(obs.get("label", obs_id), 120),
                "facts": obs,
            }
        clean_observations.append(obs)

    clean_risks = [risk_flag_record(flag) for flag in risk_flags]
    for flag in clean_risks:
        if flag["id"]:
            evidence_index[flag["id"]] = {
                "type": "risk_flag",
                "title": flag["label"],
                "facts": flag,
            }

    clean_actions = [action_record(action) for action in candidate_actions]
    for action in clean_actions:
        action_id = str(action.get("id", ""))
        if action_id:
            evidence_index[action_id] = {
                "type": "candidate_action",
                "title": str(action.get("reason", action_id)),
                "facts": action,
            }

    policy_confirmed = config_bool(config.get("agent", {}).get("policy_confirmed", False))
    policy_payload = {
        "enabled": policy_confirmed,
        "confirmed_by_user": policy_confirmed,
        "values": config.get("policy", {}) if policy_confirmed else {},
    }

    return {
        "schema_version": 1,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "privacy": {
            "contains_account_id": False,
            "contains_cookie": False,
            "contains_api_key": False,
            "amount_policy": "rounded_actual_amounts",
        },
        "ledger_summary": {
            "total_asset": rounded_money(ledger_summary.get("total_asset")),
            "total_profit": rounded_money(ledger_summary.get("total_profit")),
            "day_profit": rounded_money(ledger_summary.get("day_profit")),
        },
        "portfolio": compact_portfolio_summary(portfolio_summary, config),
        "holdings": holding_payload,
        "observations": clean_observations,
        "risk_flags": clean_risks,
        "candidate_actions": clean_actions,
        "history_diff": history_diff or {"is_first_run": True},
        "policy": policy_payload,
        "evidence_index": evidence_index,
        "instructions": {
            "only_use_input_data": True,
            "no_news_or_macro_without_evidence": True,
            "no_new_trade_actions": not config_bool(
                config.get("agent", {}).get("llm_can_create_new_actions", False)
            ),
            "cite_evidence_refs": True,
        },
    }


def agent_report_schema_hint() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "summary": {
            "health_score": "0-100 或 null",
            "status": "简短状态，如 review/fallback/need_policy",
            "brief": "一句话总结，必须基于输入数据",
        },
        "diagnosis": [
            {
                "id": "diag:unique_id",
                "title": "诊断标题",
                "severity": "low|medium|high|critical",
                "explanation": "解释数据事实和推断的关系",
                "evidence_refs": ["必须来自输入 evidence_index 的 key"],
            }
        ],
        "action_reviews": [
            {
                "candidate_action_id": "只能引用输入 candidate_actions 中已有 id",
                "stance": "support|defer|reject|need_user_rule",
                "reason": "审阅理由",
                "evidence_refs": ["必须来自输入 evidence_index 的 key"],
            }
        ],
        "holding_analysis": [
            {
                "target_code": "510300",
                "target_name": "沪深300ETF",
                "action_type": "buy|reduce|hold|watch|rebalance|classify_required",
                "title": "单标的建议标题",
                "reason": "基于该标的技术指标、仓位、收益和分类的具体解释",
                "evidence_refs": [
                    "holding:510300:technical",
                    "holding:510300:classification"
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
                "evidence_refs": ["必须来自输入 evidence_index 的 key"],
            }
        ],
        "questions": [
            {
                "id": "question:unique_id",
                "question": "需要用户确认的问题",
                "reason": "为什么需要确认",
                "evidence_refs": ["必须来自输入 evidence_index 的 key"],
            }
        ],
        "limitations": ["数据不足或边界条件"],
    }


def build_agent_report_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    schema_hint = json.dumps(agent_report_schema_hint(), ensure_ascii=False, indent=2)
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    system = (
        "你是严格基于证据的中文个人持仓分析 agent。"
        "你只能解释用户提供的 JSON 数据，不得编造新闻、宏观、政策、估值或未来收益。"
        "你不能新增不在 candidate_actions 中的直接买卖动作；没有候选动作时，只能给诊断、观察条件和需要用户确认的问题。"
        "所有诊断、动作审阅、观察条件和问题都尽量引用 evidence_refs。"
        "只输出合法 JSON，不输出 Markdown 包裹或解释。"
    )
    user = (
        "/no_think\n"
        "请基于下面的持仓上下文生成结构化诊断。输出必须符合给定 JSON schema。\n"
        "分析重点：组合集中度、资产/行业/主题暴露、分类可信度、技术趋势与持仓盈亏是否匹配、历史变化、规则候选动作是否有证据支持。\n"
        "必须为每个 holdings 中的标的输出一条 holding_analysis。holding_analysis 是单标的解释型建议，不是直接交易指令；没有用户确认策略时，不要给目标仓位或金额。\n"
        "holding_analysis.action_type 不得由本地技术指标直接推导买卖动作；技术指标只作为事实证据。没有用户确认策略或候选动作时，优先使用 watch 并解释证据边界。分类不足时使用 classify_required。\n"
        "如果证据不足，写入 limitations 或 questions，不要硬给交易结论。\n\n"
        f"输出 schema:\n{schema_hint}\n\n"
        f"输入 JSON:\n{context_json}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def llm_structured_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    llm = config.get("llm", {})
    kwargs: dict[str, Any] = {}
    if config_bool(llm.get("disable_thinking", False)):
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    mode = str(llm.get("structured_output", "auto")).strip().lower()
    if mode == "none":
        return kwargs
    if mode == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
        return kwargs
    if mode == "auto" and config_bool(llm.get("supports_response_format", False)):
        kwargs["response_format"] = {"type": "json_object"}
    return kwargs


def load_agent_report_json(text: str) -> dict[str, Any]:
    clean = strip_json_markdown(text)
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(clean[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM 输出 JSON 顶层必须是对象")
    return payload


def filter_evidence_refs(refs: list[str], evidence_index: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    valid: list[str] = []
    for ref in refs:
        key = str(ref)
        if key in evidence_index and key not in seen:
            seen.add(key)
            valid.append(key)
    return valid


def normalize_report_payload_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for section in ("diagnosis", "holding_analysis", "action_reviews", "watch_conditions", "questions"):
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


def legacy_action_item(action: CandidateAction | dict[str, Any], review: dict[str, Any] | None = None) -> dict[str, Any]:
    record = action_record(action)
    if review:
        reason = review.get("reason") or record.get("reason", "")
    else:
        reason = record.get("reason", "")
    return {
        **record,
        "target": record.get("target_name") or record.get("target_code") or record.get("target", ""),
        "reason": reason,
        "candidate_action_id": record.get("id", ""),
    }


def legacy_holding_action_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id") or f"holding:{item.get('target_code', '')}:analysis",
        "type": item.get("action_type", "watch"),
        "target": item.get("target_name") or item.get("target_code", ""),
        "target_code": item.get("target_code", ""),
        "target_name": item.get("target_name", ""),
        "reason": item.get("reason", ""),
        "source": "llm_holding_analysis",
        "requires_user_confirmation": True,
    }


def deterministic_holding_action_type(holding: dict[str, Any] | None) -> str:
    if not isinstance(holding, dict):
        return "watch"
    classification = holding.get("classification", {}) if isinstance(holding.get("classification"), dict) else {}
    if classification.get("asset_class") == "unknown":
        return "classify_required"
    return "watch"


def action_copy_for_rule(action_type: str, holding: dict[str, Any] | None) -> tuple[str, str]:
    technical = holding.get("technical", {}) if isinstance(holding, dict) and isinstance(holding.get("technical"), dict) else {}
    observations = str(technical.get("technical_observations", "") or "").strip()
    title_by_type = {
        "buy": "候选买入动作待复核",
        "reduce": "候选减仓动作待复核",
        "hold": "继续跟踪持仓",
        "watch": "基于现有数据观察",
        "rebalance": "候选再平衡动作待复核",
        "classify_required": "需要先补充分信息",
    }
    return title_by_type.get(action_type, "基于现有数据观察"), observations


def text_contradicts_action(action_type: str, title: str, reason: str) -> bool:
    text = f"{title} {reason}"
    if action_type == "reduce":
        return any(token in text for token in ("建议持有", "继续持有", "持有观察", "可分批加仓", "加仓"))
    if action_type == "buy":
        return any(token in text for token in ("减仓", "暂停加仓", "止损", "赎回"))
    if action_type == "hold":
        return any(token in text for token in ("建议减仓", "止损", "赎回", "可分批加仓", "建议加仓"))
    if action_type == "classify_required":
        return any(token in text for token in ("建议减仓", "建议加仓", "止损", "赎回"))
    return False


def normalize_holding_action_copy(item: dict[str, Any], holding: dict[str, Any] | None) -> dict[str, Any]:
    action_type = str(item.get("action_type", "watch"))
    title = str(item.get("title", "") or "")
    reason = str(item.get("reason", "") or "")
    if not text_contradicts_action(action_type, title, reason):
        return item
    fallback_title, observation_reason = action_copy_for_rule(action_type, holding)
    original_reason = reason.strip()
    item["title"] = fallback_title
    if observation_reason:
        item["reason"] = observation_reason
        if original_reason and original_reason != observation_reason:
            item["reason"] = f"{observation_reason} 原始模型说明：{original_reason}"
    else:
        item["reason"] = original_reason or "动作类型已按结构化校验修正。"
    return item


def fallback_holding_analysis_from_context(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    analysis: list[dict[str, Any]] = []
    for holding in holdings:
        code = str(holding.get("code", ""))
        name = str(holding.get("name", ""))
        technical = holding.get("technical", {}) if isinstance(holding.get("technical"), dict) else {}
        classification = holding.get("classification", {}) if isinstance(holding.get("classification"), dict) else {}
        observations = str(technical.get("technical_observations", "") or "")
        action_type = deterministic_holding_action_type(holding)
        if observations:
            reason = observations
        elif classification.get("asset_class") == "unknown":
            reason = "分类不足，先确认标的资产类别、行业和策略属性。"
        else:
            reason = "现有技术指标未提供足够事实，需要结合市场环境和用户策略判断。"
        analysis.append({
            "target_code": code,
            "target_name": name,
            "action_type": action_type,
            "title": "基于现有数据观察" if action_type == "watch" else "需要先补充分信息",
            "reason": reason,
            "evidence_refs": filter_evidence_refs(
                [f"holding:{code}:technical", f"holding:{code}:classification"],
                {
                    f"holding:{code}:technical": True,
                    f"holding:{code}:classification": True,
                },
            ),
        })
    return analysis


def build_detailed_analysis(report: dict[str, Any]) -> str:
    lines: list[str] = []
    brief = report.get("summary", {}).get("brief")
    if brief:
        lines.extend(["### 总结", "", str(brief), ""])
    if report.get("diagnosis"):
        lines.extend(["### 诊断", ""])
        for item in report["diagnosis"]:
            severity = item.get("severity", "medium")
            title = item.get("title") or item.get("id") or "未命名诊断"
            explanation = item.get("explanation", "")
            lines.append(f"- [{severity}] {title}: {explanation}")
        lines.append("")
    if report.get("action_reviews"):
        lines.extend(["### 候选动作审阅", ""])
        for item in report["action_reviews"]:
            lines.append(
                f"- {item.get('candidate_action_id', '')}: {item.get('stance', '')}，{item.get('reason', '')}"
            )
        lines.append("")
    if report.get("holding_analysis"):
        lines.extend(["### 单标的建议", ""])
        for item in report["holding_analysis"]:
            target = item.get("target_name") or item.get("target_code") or "未知标的"
            action_type = item.get("action_type", "watch")
            reason = item.get("reason", "")
            lines.append(f"- {target} [{action_type}]: {reason}")
        lines.append("")
    if report.get("watch_conditions"):
        lines.extend(["### 观察条件", ""])
        for item in report["watch_conditions"]:
            target = item.get("target_code") or "组合"
            lines.append(f"- {target} {item.get('metric', '')}: {item.get('condition', '')}")
        lines.append("")
    if report.get("questions"):
        lines.extend(["### 需要确认", ""])
        for item in report["questions"]:
            lines.append(f"- {item.get('question', '')}")
        lines.append("")
    if report.get("limitations"):
        lines.extend(["### 限制", ""])
        for item in report["limitations"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).strip()


def validate_agent_report(
    payload: dict[str, Any],
    candidate_actions: list[CandidateAction | dict[str, Any]],
    evidence_index: dict[str, Any],
    config: dict[str, Any],
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = normalize_report_payload_aliases(payload)
    try:
        report_model = AgentReport.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"LLM 报告 schema 校验失败: {exc}") from exc

    report = report_model.model_dump()
    action_map = {str(action_record(action).get("id", "")): action for action in candidate_actions}
    questions = list(report["questions"])

    for index, item in enumerate(report["diagnosis"], start=1):
        if not item["id"]:
            item["id"] = f"diagnosis:{index}"
        if item["severity"] not in VALID_SEVERITIES:
            item["severity"] = "medium"
        item["evidence_refs"] = filter_evidence_refs(item.get("evidence_refs", []), evidence_index)

    holding_by_code = {str(item.get("code", "")): item for item in (holdings or [])}
    valid_holding_analysis: list[dict[str, Any]] = []
    seen_holding_codes: set[str] = set()
    for item in report["holding_analysis"]:
        code = str(item.get("target_code", ""))
        if code and holding_by_code and code not in holding_by_code:
            questions.append({
                "id": f"question:unknown_holding:{code}",
                "question": f"LLM 提到了当前持仓中不存在的标的 {code}，需要确认数据来源。",
                "reason": item.get("reason", "未知标的不能进入单标的建议"),
                "evidence_refs": filter_evidence_refs(item.get("evidence_refs", []), evidence_index),
            })
            continue
        if item["action_type"] not in VALID_HOLDING_ACTION_TYPES:
            item["action_type"] = "watch"
        deterministic_action_type = deterministic_holding_action_type(holding_by_code.get(code))
        if deterministic_action_type == "classify_required":
            item["action_type"] = deterministic_action_type
        if not item.get("target_name") and code in holding_by_code:
            item["target_name"] = str(holding_by_code[code].get("name", ""))
        if not item.get("title"):
            item["title"] = "基于现有数据观察"
        item = normalize_holding_action_copy(item, holding_by_code.get(code))
        item["evidence_refs"] = filter_evidence_refs(item.get("evidence_refs", []), evidence_index)
        if code:
            seen_holding_codes.add(code)
        valid_holding_analysis.append(item)

    for fallback_item in fallback_holding_analysis_from_context(holdings or []):
        code = str(fallback_item.get("target_code", ""))
        if code and code not in seen_holding_codes:
            fallback_item["evidence_refs"] = filter_evidence_refs(
                fallback_item.get("evidence_refs", []),
                evidence_index,
            )
            valid_holding_analysis.append(fallback_item)
    report["holding_analysis"] = valid_holding_analysis

    valid_reviews: list[dict[str, Any]] = []
    llm_can_create = config_bool(config.get("agent", {}).get("llm_can_create_new_actions", False))
    for index, item in enumerate(report["action_reviews"], start=1):
        action_id = str(item.get("candidate_action_id", ""))
        if action_id.strip().lower() in {"", "none", "null", "n/a"}:
            continue
        if action_id not in action_map and not llm_can_create:
            questions.append({
                "id": f"question:unknown_action:{index}",
                "question": f"LLM 提到了未知候选动作 {action_id}，需要先由规则引擎或用户确认。",
                "reason": item.get("reason", "未知动作不能直接进入建议"),
                "evidence_refs": filter_evidence_refs(item.get("evidence_refs", []), evidence_index),
            })
            continue
        if item["stance"] not in VALID_ACTION_STANCES:
            item["stance"] = "need_user_rule"
        item["evidence_refs"] = filter_evidence_refs(item.get("evidence_refs", []), evidence_index)
        valid_reviews.append(item)
    report["action_reviews"] = valid_reviews

    for index, item in enumerate(report["watch_conditions"], start=1):
        if not item["id"]:
            item["id"] = f"watch:{index}"
        item["evidence_refs"] = filter_evidence_refs(item.get("evidence_refs", []), evidence_index)

    for index, item in enumerate(questions, start=1):
        if not item.get("id"):
            item["id"] = f"question:{index}"
        item["evidence_refs"] = filter_evidence_refs(item.get("evidence_refs", []), evidence_index)
    report["questions"] = questions

    used_refs: set[str] = set()
    for section in ("diagnosis", "holding_analysis", "action_reviews", "watch_conditions", "questions"):
        for item in report.get(section, []):
            used_refs.update(item.get("evidence_refs", []))
    report["evidence"] = sorted(used_refs)

    report["risk_tags"] = [
        item["title"]
        for item in report["diagnosis"]
        if item.get("title") and item.get("severity") in {"medium", "high", "critical"}
    ]
    report["action_items"] = [
        legacy_action_item(action_map[item["candidate_action_id"]], item)
        for item in report["action_reviews"]
        if item.get("candidate_action_id") in action_map
    ]
    report["action_items"].extend(
        legacy_holding_action_item(item)
        for item in report["holding_analysis"]
    )
    report["detailed_analysis"] = build_detailed_analysis(report)
    return report


def repair_agent_report_json(
    raw_text: str,
    error: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    attempts = int(config.get("llm", {}).get("repair_attempts", 1) or 0)
    if attempts <= 0:
        return None
    schema_hint = json.dumps(agent_report_schema_hint(), ensure_ascii=False, indent=2)
    prompt = (
        "下面是一段需要修复的 JSON。只输出修复后的合法 JSON，不要解释，不要 Markdown 包裹。\n"
        f"解析错误: {error}\n\n"
        f"目标 schema:\n{schema_hint}\n\n"
        f"原始内容:\n{raw_text}"
    )
    messages = [
        {"role": "system", "content": "你只修复 JSON 语法和结构，只输出合法 JSON。"},
        {"role": "user", "content": prompt},
    ]
    for _ in range(attempts):
        try:
            fixed = call_llm(messages, config, request_kwargs=llm_structured_kwargs(config))
            return load_agent_report_json(fixed)
        except Exception as exc:  # noqa: BLE001
            log(f"LLM JSON 修复失败: {exc}", level="WARN", name="agent_llm")
    return None


def parse_agent_report(
    text: str,
    candidate_actions: list[CandidateAction | dict[str, Any]],
    evidence_index: dict[str, Any],
    config: dict[str, Any],
    observations: list[dict[str, Any]] | None = None,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        payload = load_agent_report_json(text)
    except Exception as exc:  # noqa: BLE001
        repaired = repair_agent_report_json(text, str(exc), config)
        if repaired is None:
            return fallback_agent_report(
                candidate_actions,
                observations or [],
                "LLM 输出不是合法 JSON，且修复失败",
                holdings or [],
            )
        payload = repaired

    try:
        return validate_agent_report(payload, candidate_actions, evidence_index, config, holdings=holdings)
    except Exception as exc:  # noqa: BLE001
        log(f"LLM 报告校验失败，使用回退报告: {exc}", level="WARN", name="agent_llm")
        return fallback_agent_report(candidate_actions, observations or [], str(exc), holdings or [])


def fallback_agent_report(
    candidate_actions: list[CandidateAction | dict[str, Any]],
    observations: list[dict[str, Any]],
    reason: str,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    action_items = [legacy_action_item(action) for action in candidate_actions]
    holding_analysis = fallback_holding_analysis_from_context(holdings or [])
    action_items.extend(legacy_holding_action_item(item) for item in holding_analysis)
    watch_conditions = [
        {
            "id": str(item.get("id", f"watch:{index}")),
            "target_code": "",
            "metric": str(item.get("type", "observation")),
            "condition": str(item.get("label", "")),
            "reason": "；".join(str(part) for part in item.get("evidence", [])),
            "evidence_refs": [str(item.get("id", ""))] if item.get("id") else [],
        }
        for index, item in enumerate(observations, start=1)
    ]
    report = {
        "schema_version": 1,
        "summary": {
            "health_score": None,
            "status": "fallback",
            "brief": f"AI 诊断失败，已返回规则引擎结果: {reason}",
        },
        "diagnosis": [],
        "holding_analysis": holding_analysis,
        "action_reviews": [],
        "watch_conditions": watch_conditions,
        "questions": [],
        "limitations": [reason],
        "evidence": [],
        "risk_tags": [],
        "action_items": action_items,
    }
    report["detailed_analysis"] = build_detailed_analysis(report)
    return report


def generate_agent_report_with_llm(
    context: dict[str, Any],
    config: dict[str, Any],
    model_override: str | None = None,
) -> dict[str, Any]:
    candidate_actions = list(context.get("candidate_actions", []))
    observations = list(context.get("observations", []))
    holdings = list(context.get("holdings", []))
    if not llm_enabled(config):
        return fallback_agent_report(candidate_actions, observations, "LLM 未启用", holdings)

    try:
        answer = call_llm(
            build_agent_report_messages(context),
            config,
            model_override=model_override,
            request_kwargs=llm_structured_kwargs(config),
        )
    except Exception as exc:  # noqa: BLE001
        log(f"LLM 报告调用失败: {exc}", level="ERROR", name="agent_llm")
        return fallback_agent_report(candidate_actions, observations, f"LLM 调用失败: {exc}", holdings)

    return parse_agent_report(
        answer,
        candidate_actions,
        context.get("evidence_index", {}),
        config,
        observations=observations,
        holdings=holdings,
    )
