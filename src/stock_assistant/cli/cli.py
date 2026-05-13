import argparse
import json
import sys
from pathlib import Path
from typing import Any

from stock_assistant.core.config import DEFAULT_CONFIG, ensure_dirs, load_config
from stock_assistant.core.models import Holding, InstrumentClassification
from stock_assistant.core.utils import log
from stock_assistant.integrations.tzzb import fetch_tzzb_holdings
from stock_assistant.services.classification import classify_holding, classification_from_config, load_cached_classification
from stock_assistant.services.analysis import analyze_holdings
from stock_assistant.core.llm import llm_enabled, generate_llm_commentary
from stock_assistant.services.report import write_report, report_markdown
from stock_assistant.services.portfolio import summarize_portfolio, generate_portfolio_observations

def run(config: dict[str, Any]) -> Path:
    ensure_dirs(config)
    holdings, archived, _ = fetch_tzzb_holdings(config)
    log(f"解析到持仓数量: {len(holdings)}")
    
    for holding in holdings:
        classify_holding(holding, config)
        
    results = analyze_holdings(holdings, config)
    llm_commentary = None
    if llm_enabled(config):
        log(f"请求 LLM 解读: {config['llm']['base_url']} model={config['llm']['model']}")
        try:
            llm_commentary = generate_llm_commentary(results, config)
        except Exception as exc:  # noqa: BLE001
            llm_commentary = f"AI 解读失败: `{exc}`"
            log(llm_commentary)
    return write_report(report_markdown(results, archived, llm_commentary), config)

def load_profile_holdings(config: dict[str, Any]) -> tuple[list[Holding], Path | None, dict[str, Any]]:
    ensure_dirs(config)
    holdings, source, summary = fetch_tzzb_holdings(config)
    return holdings, source, summary

def profile_classification_for_holding(
    holding: Holding,
    config: dict[str, Any],
    refresh_classification: bool = False,
) -> InstrumentClassification:
    if refresh_classification:
        return classify_holding(holding, config)
    return (
        classification_from_config(holding, config)
        or load_cached_classification(holding, config)
        or InstrumentClassification(code=holding.code, name=holding.name, source="unknown")
    )

def build_portfolio_profile(
    config: dict[str, Any],
    refresh_classification: bool = False,
) -> dict[str, Any]:
    holdings, source, ledger_summary = load_profile_holdings(config)
    log(f"解析到持仓数量: {len(holdings)}")
    classifications = {
        holding.code: profile_classification_for_holding(holding, config, refresh_classification)
        for holding in holdings
    }
    summary = summarize_portfolio(holdings, classifications, config)
    observations = generate_portfolio_observations(summary)
    return {
        "source": str(source) if source is not None else None,
        "ledger_summary": ledger_summary,
        "summary": summary,
        "observations": observations,
        "classifications": {
            code: {
                "code": classification.code,
                "name": classification.name,
                "asset_class": classification.asset_class,
                "sector": classification.sector,
                "theme": classification.theme,
                "region": classification.region,
                "strategy": classification.strategy,
                "tracked_index": classification.tracked_index,
                "issuer": classification.issuer,
                "confidence": classification.confidence,
                "source": classification.source,
                "reviewed_by_user": classification.reviewed_by_user,
            }
            for code, classification in classifications.items()
        },
    }

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="每日 ETF 持仓分析工具")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径，默认 ./config.toml")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="通过 API 获取持仓并生成报告")

    profile_parser = subparsers.add_parser("profile", help="输出组合画像和事实观察项 JSON")
    profile_parser.add_argument(
        "--refresh-classification",
        action="store_true",
        help="允许补全分类：可能触发搜索和分类 LLM；默认只读已有配置/缓存",
    )
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        print(
            "\n示例:\n"
            "  python3 -m stock_assistant.cli run\n"
            "  python3 -m stock_assistant.cli --config config.toml profile\n",
            file=sys.stderr,
        )
        return 2
    config = load_config(Path(args.config).expanduser())
    command = args.command
    
    if command == "profile":
        profile = build_portfolio_profile(
            config,
            refresh_classification=bool(getattr(args, "refresh_classification", False)),
        )
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
        
    # 默认为 run
    report = run(config)
    print(report)
    return 0

if __name__ == "__main__":
    sys.exit(main())
