import datetime as dt
import json
from pathlib import Path
from typing import Any, AsyncIterator

from stock_assistant.agents.agent_llm import build_agent_llm_context, generate_agent_report_with_llm
from stock_assistant.services.analysis import analyze_one
from stock_assistant.services.classification import classification_from_config, load_cached_classification
from stock_assistant.services.market import fetch_bars
from stock_assistant.core.memory import (
    agent_snapshots_have_same_facts,
    build_agent_snapshot,
    diff_agent_snapshots,
    load_latest_agent_snapshot,
    save_agent_snapshot,
)
from stock_assistant.core.models import Holding, InstrumentClassification, analysis_result_to_dict, holding_to_dict
from stock_assistant.services.portfolio import generate_portfolio_observations, summarize_portfolio
from stock_assistant.integrations.tzzb import fetch_tzzb_holdings
from stock_assistant.core.utils import config_bool, log


def agent_event(step: str, status: str = "", **extra: Any) -> dict[str, Any]:
    event = {"step": step}
    if status:
        event["status"] = status
    event.update(extra)
    return event


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


async def run_agent_analysis_events(
    config: dict[str, Any],
    holdings: list[Holding] | None = None,
    cached_results: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
    save_snapshot: bool = True,
    save_report: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    model = model_override or config.get("llm", {}).get("model", "unknown")
    source = "provided"
    ledger_summary: dict[str, Any] = {}
    technical_results: list[dict[str, Any]] = []

    if cached_results is not None:
        holdings = holdings_from_results(cached_results)
        technical_results = cached_results
        source = "cached_results"
        yield agent_event(
            "technical_analysis",
            f"检测到已有的技术分析数据，跳过行情拉取，直接进入 AI 诊断 (模型: {model})",
            technical_results=technical_results,
        )
    else:
        if holdings is None:
            if str(config.get("ledger", {}).get("mode", "")).strip().lower() != "tzzb_api":
                raise RuntimeError("agent 模式当前需要 ledger.mode=tzzb_api 或传入 holdings")
            yield agent_event("sync_holdings", "正在同步投资账本")
            holdings, source_path, ledger_summary = fetch_tzzb_holdings(config)
            source = str(source_path)
            yield agent_event("sync_holdings", f"已获取 {len(holdings)} 个标的")

        total_value = sum(item.market_value or 0 for item in holdings) or None
        for index, holding in enumerate(holdings, start=1):
            if holding.asset_type == "fund":
                technical_results.append(fund_analysis_result(holding, total_value))
                continue

            yield agent_event(
                "market_data",
                f"[{index}/{len(holdings)}] 正在拉取 {holding.name} ({holding.code}) 的行情数据",
                code=holding.code,
                name=holding.name,
            )
            try:
                bars = fetch_bars(holding.code, config)
                technical_results.append(analysis_result_to_dict(analyze_one(holding, bars, config, total_value)))
            except Exception as exc:  # noqa: BLE001
                log(f"分析 {holding.code} 失败: {exc}", level="WARN", name="agent")
                technical_results.append({
                    "holding": holding_to_dict(holding),
                    "ok": False,
                    "action": "行情失败",
                    "reason": str(exc),
                })

        yield agent_event(
            "technical_analysis",
            "技术分析完成，已暂存中间数据",
            technical_results=technical_results,
        )

    holdings = holdings or []
    yield agent_event("classify", "正在分类持仓")
    classifications = {holding.code: classify_for_agent(holding, config) for holding in holdings}

    yield agent_event("portfolio_profile", "正在生成组合画像")
    summary = summarize_portfolio(holdings, classifications, config)

    yield agent_event("portfolio_observations", "正在生成组合观察项")
    observations = generate_portfolio_observations(summary)

    risk_flags: list[Any] = []
    candidate_actions: list[Any] = []
    previous_snapshot = load_latest_agent_snapshot(config)
    current_facts = {
        "ledger_summary": ledger_summary,
        "portfolio": summary,
        "classifications": classifications,
        "technical_results": technical_results,
        "risk_flags": risk_flags,
        "candidate_actions": candidate_actions,
    }
    history_diff = diff_agent_snapshots(previous_snapshot, current_facts)

    yield agent_event("llm_report", f"正在请求 LLM 生成解释 ({model})")
    llm_context = build_agent_llm_context(
        holdings=holdings,
        classifications=classifications,
        technical_results=technical_results,
        portfolio_summary=summary,
        observations=observations,
        risk_flags=risk_flags,
        candidate_actions=candidate_actions,
        history_diff=history_diff,
        ledger_summary=ledger_summary,
        config=config,
    )
    agent_report = generate_agent_report_with_llm(llm_context, config, model_override=model_override)

    snapshot = build_agent_snapshot(
        source=source,
        ledger_summary=ledger_summary,
        holdings=holdings,
        classifications=classifications,
        technical_results=technical_results,
        summary=summary,
        observations=observations,
        risk_flags=risk_flags,
        candidate_actions=candidate_actions,
        agent_report=agent_report,
        model=model,
    )

    yield agent_event("save_snapshot", "正在保存快照")
    if agent_snapshots_have_same_facts(previous_snapshot, snapshot):
        log("Agent 快照事实未变化，跳过重复保存。", name="agent")
    elif save_snapshot and config_bool(config.get("agent", {}).get("save_snapshots", True)):
        save_agent_snapshot(snapshot, config)

    if save_report:
        save_ai_report(technical_results, agent_report, model, config)

    yield agent_event("done", "Agent 分析完成", snapshot=snapshot)


async def run_agent_analysis(
    config: dict[str, Any],
    holdings: list[Holding] | None = None,
    cached_results: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
    save_snapshot: bool = True,
    save_report: bool = True,
) -> dict[str, Any]:
    snapshot: dict[str, Any] | None = None
    async for event in run_agent_analysis_events(
        config,
        holdings=holdings,
        cached_results=cached_results,
        model_override=model_override,
        save_snapshot=save_snapshot,
        save_report=save_report,
    ):
        if event.get("step") == "done":
            snapshot = event.get("snapshot")
    if snapshot is None:
        raise RuntimeError("agent 分析没有生成 snapshot")
    return snapshot
