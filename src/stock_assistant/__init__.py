import csv
from pathlib import Path
from typing import Any

from .services.analysis import analyze_holdings, analyze_one, decide_action
from .agents.agent_loop import run_tool_agent_events
from .agents.agent_llm import build_agent_llm_context, generate_agent_report_with_llm, parse_agent_report, fallback_agent_report, llm_structured_kwargs
from .core.llm_tools import parse_llm_tool_step, LlmToolCall, LlmToolStep
from .services.classification import classification_from_config, classify_holding, load_cached_classification, save_classification_cache, classification_cache_is_fresh, classification_cache_status
from .core.config import DEFAULTS, DEFAULT_CONFIG, ensure_dirs, load_config
from .core.llm import generate_structured_llm_commentary, llm_enabled, generate_llm_commentary
from .services.market import fetch_bars
from .core.memory import (
    save_agent_snapshot,
    load_latest_agent_snapshot,
    diff_agent_snapshots,
    build_agent_snapshot,
    list_agent_snapshots,
    agent_snapshot_fingerprint,
    agent_snapshots_have_same_facts,
)
from .core.models import Bar, Holding, InstrumentClassification, RiskFlag, CandidateAction, analysis_result_to_dict, holding_to_dict, bar_to_dict
from .services.portfolio import generate_portfolio_observations, summarize_portfolio, value_map_to_pct
from .services.report import report_markdown, write_report
from .integrations.search import build_search_provider, suggest_classification_with_search, TavilySearchProvider, BraveSearchProvider, OpenCliSearchProvider, score_classification_evidence
from .integrations.tzzb import extract_cookie_from_curl, fetch_tzzb_holdings, tzzb_stock_holding
from .core.utils import extract_code, load_env_file, log, parse_number, pick_value


def parse_holdings(path: str | Path, config: dict[str, Any]) -> list[Holding]:
    columns = config.get("columns", {})
    code_aliases = columns.get("code", "证券代码,基金代码,代码,产品代码,symbol,code")
    name_aliases = columns.get("name", "证券名称,基金名称,名称,产品名称,name")
    quantity_aliases = columns.get("quantity", "持仓数量,可用份额,持有份额,数量,份额")
    cost_aliases = columns.get("cost_price", "成本价,持仓成本价,买入均价,成本")
    value_aliases = columns.get("market_value", "持仓市值,市值,最新市值")
    profit_aliases = columns.get("profit_pct", "收益率,持仓收益率,盈亏比例")

    rows: list[Holding] = []
    with Path(path).expanduser().open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            code = extract_code(pick_value(row, code_aliases))
            name = pick_value(row, name_aliases)
            if not code or not name:
                continue
            rows.append(
                Holding(
                    code=code,
                    name=name,
                    quantity=parse_number(pick_value(row, quantity_aliases)),
                    cost_price=parse_number(pick_value(row, cost_aliases)),
                    market_value=parse_number(pick_value(row, value_aliases)),
                    profit_pct=parse_number(pick_value(row, profit_aliases)),
                    source_row=dict(row),
                )
            )
    return rows


def archive_holding_file(path: str | Path, config: dict[str, Any]) -> Path:
    _ = config
    return Path(path).expanduser()


def run(config: dict[str, Any], holdings_file: str | Path | None = None) -> Path:
    if holdings_file is None:
        ensure_dirs(config)
        holdings, archived, _ = fetch_tzzb_holdings(config)
    else:
        archived = Path(holdings_file)
        holdings = parse_holdings(archived, config)
    results = analyze_holdings(holdings, config)
    llm_commentary = generate_llm_commentary(results, config) if llm_enabled(config) else None
    return write_report(report_markdown(results, archived, llm_commentary), config)

__all__ = [
    "Holding", "Bar", "InstrumentClassification",
    "holding_to_dict", "bar_to_dict", "analysis_result_to_dict",
    "summarize_portfolio", "generate_portfolio_observations", "value_map_to_pct",
    "log", "load_config", "ensure_dirs", "load_env_file",
    "DEFAULTS", "DEFAULT_CONFIG",
    "fetch_tzzb_holdings", "extract_cookie_from_curl", "tzzb_stock_holding",
    "fetch_bars", "analyze_holdings", "analyze_one", "decide_action",
    "llm_enabled", "generate_structured_llm_commentary", "generate_llm_commentary",
    "build_agent_llm_context", "generate_agent_report_with_llm", "parse_agent_report", "fallback_agent_report", "llm_structured_kwargs",
    "run_tool_agent_events",
    "parse_llm_tool_step", "LlmToolCall", "LlmToolStep",
    "agent_snapshot_fingerprint", "agent_snapshots_have_same_facts",
    "classification_from_config", "classify_holding", "load_cached_classification", "save_classification_cache", "classification_cache_is_fresh", "classification_cache_status",
    "build_search_provider", "suggest_classification_with_search", "TavilySearchProvider", "BraveSearchProvider", "OpenCliSearchProvider", "score_classification_evidence",
    "parse_holdings", "archive_holding_file", "report_markdown", "write_report", "run"
]
