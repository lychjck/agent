"""共用辅助函数。

历史上这里曾承载非工具 Agent 的"流水线"主流程 (`run_agent_analysis_events`)，
但前端只走 tool_agent 路径，那条流水线已无人调用。本文件目前只保留几个被
工具 Agent 复用的小工具函数，避免在多个地方各写一份。
"""
import datetime as dt
import json
from pathlib import Path
from typing import Any

from stock_assistant.core.models import Holding, InstrumentClassification, holding_to_dict
from stock_assistant.core.utils import log
from stock_assistant.services.classification import classification_from_config, load_cached_classification


def holding_from_result(result: dict[str, Any]) -> Holding | None:
    data = result.get("holding", {})
    if not isinstance(data, dict):
        return None
    valid_keys = {
        "code",
        "name",
        "quantity",
        "cost_price",
        "market_value",
        "profit_pct",
        "hold_profit",
        "day_profit",
        "asset_type",
    }
    kwargs = {key: value for key, value in data.items() if key in valid_keys}
    if "code" not in kwargs or "name" not in kwargs:
        return None
    return Holding(**kwargs)


def holdings_from_results(results: list[dict[str, Any]]) -> list[Holding]:
    holdings: list[Holding] = []
    for result in results:
        holding = holding_from_result(result)
        if holding is not None:
            holdings.append(holding)
    return holdings


def classify_for_agent(holding: Holding, config: dict[str, Any]) -> InstrumentClassification:
    return (
        classification_from_config(holding, config)
        or load_cached_classification(holding, config)
        or InstrumentClassification(code=holding.code, name=holding.name, source="unknown")
    )


def fund_analysis_result(holding: Holding, total_value: float | None) -> dict[str, Any]:
    return {
        "holding": holding_to_dict(holding),
        "ok": True,
        "action": "持有场外基金",
        "reason": "场外基金，不参与K线分析",
        "profit_pct": holding.profit_pct,
        "current_value": holding.market_value,
        "weight": holding.market_value / total_value * 100 if holding.market_value and total_value else None,
    }


def save_ai_report(
    technical_results: list[dict[str, Any]],
    agent_report: dict[str, Any],
    model: str,
    config: dict[str, Any],
) -> Path:
    report_dir = Path(config.get("paths", {}).get("report_dir", "reports")).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"ai-report-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(
        json.dumps(
            {
                "model": model,
                "results": technical_results,
                "ai_response": agent_report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"AI 诊断报告已保存至: {path}")
    return path
