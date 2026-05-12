import argparse
import json
import sys
from pathlib import Path
from typing import Any

import stock_assistant as sa
from .config import DEFAULT_CONFIG, ensure_dirs, load_config
from .models import Holding, InstrumentClassification
from .utils import log

def run(config: dict[str, Any], holdings_file: Path | None) -> Path:
    ensure_dirs(config)
    if holdings_file:
        log(f"使用指定持仓文件: {holdings_file}")
        source = holdings_file
        archived = sa.archive_holding_file(source, config)
        holdings = sa.parse_holdings(archived, config)
    elif str(config.get("ledger", {}).get("mode", "")).strip().lower() == "tzzb_api":
        holdings, archived, _ = sa.fetch_tzzb_holdings(config)
    else:
        source = sa.open_ledger_and_download(config)
        archived = sa.archive_holding_file(source, config)
        holdings = sa.parse_holdings(archived, config)
    log(f"解析到持仓数量: {len(holdings)}")
    
    for holding in holdings:
        sa.classify_holding(holding, config)
        
    results = sa.analyze_holdings(holdings, config)
    llm_commentary = None
    if sa.llm_enabled(config):
        log(f"请求 LLM 解读: {config['llm']['base_url']} model={config['llm']['model']}")
        try:
            llm_commentary = sa.generate_llm_commentary(results, config)
        except Exception as exc:  # noqa: BLE001
            llm_commentary = f"AI 解读失败: `{exc}`"
            log(llm_commentary)
    return sa.write_report(sa.report_markdown(results, archived, llm_commentary), config)

def load_profile_holdings(config: dict[str, Any], holdings_file: Path | None) -> tuple[list[Holding], Path | None, dict[str, Any]]:
    ensure_dirs(config)
    if holdings_file:
        log(f"使用指定持仓文件: {holdings_file}")
        archived = sa.archive_holding_file(holdings_file, config)
        return sa.parse_holdings(archived, config), archived, {}
    if str(config.get("ledger", {}).get("mode", "")).strip().lower() == "tzzb_api":
        holdings, source, summary = sa.fetch_tzzb_holdings(config)
        return holdings, source, summary
    source = sa.open_ledger_and_download(config)
    archived = sa.archive_holding_file(source, config)
    return sa.parse_holdings(archived, config), archived, {}

def profile_classification_for_holding(
    holding: Holding,
    config: dict[str, Any],
    refresh_classification: bool = False,
) -> InstrumentClassification:
    if refresh_classification:
        return sa.classify_holding(holding, config)
    return (
        sa.classification_from_config(holding, config)
        or sa.load_cached_classification(holding, config)
        or sa.InstrumentClassification(code=holding.code, name=holding.name, source="unknown")
    )

def build_portfolio_profile(
    config: dict[str, Any],
    holdings_file: Path | None = None,
    refresh_classification: bool = False,
) -> dict[str, Any]:
    holdings, source, ledger_summary = load_profile_holdings(config, holdings_file)
    log(f"解析到持仓数量: {len(holdings)}")
    classifications = {
        holding.code: profile_classification_for_holding(holding, config, refresh_classification)
        for holding in holdings
    }
    summary = sa.summarize_portfolio(holdings, classifications, config)
    observations = sa.generate_portfolio_observations(summary)
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

    run_parser = subparsers.add_parser("run", help="打开账本/读取持仓并生成报告")
    run_parser.add_argument("--holdings", help="跳过浏览器下载，直接分析指定持仓 CSV/XLSX")

    profile_parser = subparsers.add_parser("profile", help="输出组合画像和事实观察项 JSON")
    profile_parser.add_argument("--holdings", help="跳过账本同步，直接画像指定持仓 CSV/XLSX")
    profile_parser.add_argument(
        "--refresh-classification",
        action="store_true",
        help="允许补全分类：可能触发搜索和分类 LLM；默认只读已有配置/缓存",
    )

    subparsers.add_parser("download", help="只打开账本并等待持仓文件下载")

    analyze_parser = subparsers.add_parser("analyze", help="只分析指定持仓 CSV/XLSX")
    analyze_parser.add_argument("holdings", help="持仓 CSV/XLSX 文件")
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        print(
            "\n示例:\n"
            "  python3 -m stock_assistant.cli analyze tests/fixtures/holdings.csv\n"
            "  python3 -m stock_assistant.cli --config config.toml run\n"
            "\n说明: 直接 run 会等待你从投资账本导出新的 CSV/XLSX 持仓文件。\n",
            file=sys.stderr,
        )
        return 2
    config = load_config(Path(args.config).expanduser())
    command = args.command
    if command == "download":
        ensure_dirs(config)
        print(open_ledger_and_download(config))
        return 0
    if command == "analyze":
        report = run(config, Path(args.holdings).expanduser())
        print(report)
        return 0
    if command == "profile":
        holdings = Path(args.holdings).expanduser() if getattr(args, "holdings", None) else None
        profile = build_portfolio_profile(
            config,
            holdings,
            refresh_classification=bool(getattr(args, "refresh_classification", False)),
        )
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
    holdings = Path(args.holdings).expanduser() if getattr(args, "holdings", None) else None
    report = run(config, holdings)
    print(report)
    return 0

if __name__ == "__main__":
    sys.exit(main())
