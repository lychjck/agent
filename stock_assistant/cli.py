import argparse
import sys
from pathlib import Path
from typing import Any

import stock_assistant as sa
from .config import DEFAULT_CONFIG, ensure_dirs, load_config
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

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="每日 ETF 持仓分析工具")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径，默认 ./config.toml")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="打开账本/读取持仓并生成报告")
    run_parser.add_argument("--holdings", help="跳过浏览器下载，直接分析指定持仓 CSV/XLSX")

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
    holdings = Path(args.holdings).expanduser() if getattr(args, "holdings", None) else None
    report = run(config, holdings)
    print(report)
    return 0

if __name__ == "__main__":
    sys.exit(main())
