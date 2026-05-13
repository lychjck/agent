import datetime as dt
from pathlib import Path
from typing import Any

from stock_assistant.core.models import Bar, Holding
from stock_assistant.core.utils import fmt, log

def report_markdown(results: list[dict[str, Any]], source_file: Path, llm_commentary: str | None = None) -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    ok_count = sum(1 for item in results if item.get("ok"))
    lines = [
        f"# ETF 持仓日报 - {today}",
        "",
        f"- 持仓文件: `{source_file}`",
        f"- 已分析: {ok_count}/{len(results)}",
        "- 说明: 本报告是基于 K 线和持仓数据的规则化风险提示，不构成投资建议或收益承诺。",
        "",
        "## 总览",
        "",
        "| 代码 | 名称 | 最新价 | 5日 | 20日 | 持仓收益 | 仓位 | 动作 | 依据 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in results:
        holding: Holding = item["holding"]
        latest = item.get("latest")
        lines.append(
            "| {code} | {name} | {price} | {ret5} | {ret20} | {profit} | {weight} | {action} | {reason} |".format(
                code=holding.code,
                name=holding.name,
                price=fmt(latest.close if latest else None),
                ret5=fmt(item.get("ret5"), "%"),
                ret20=fmt(item.get("ret20"), "%"),
                profit=fmt(item.get("profit_pct"), "%"),
                weight=fmt(item.get("weight"), "%"),
                action=item.get("action", "-"),
                reason=str(item.get("reason", "-")).replace("|", "/"),
            )
        )

    if llm_commentary:
        lines.extend(["", "## AI 综合解读", "", llm_commentary.strip(), ""])

    lines.extend(["", "## 明细", ""])
    for item in results:
        holding = item["holding"]
        lines.extend([f"### {holding.code} {holding.name}", ""])
        if not item.get("ok") or "latest" not in item:
            lines.extend([f"- 状态: {item.get('action')}", f"- 原因: {item.get('reason')}", ""])
            continue
        latest: Bar = item["latest"]
        lines.extend(
            [
                f"- 最新交易日: {latest.date}, 收盘价: {fmt(latest.close)}, 当日涨跌: {fmt(latest.pct_change, '%')}",
                f"- 均线: MA20={fmt(item.get('ma20'))}, MA60={fmt(item.get('ma60'))}, MA120={fmt(item.get('ma120'))}",
                f"- 动量: 5日={fmt(item.get('ret5'), '%')}, 20日={fmt(item.get('ret20'), '%')}, RSI14={fmt(item.get('rsi14'))}",
                f"- 风险: 120日高点回撤={fmt(item.get('drawdown'), '%')}, 20日波动率={fmt(item.get('vol20'), '%')}, 量比={fmt(item.get('vol_ratio'), '', 2)}",
                f"- 建议动作: {item.get('action')}",
                f"- 依据: {item.get('reason')}",
                "",
            ]
        )
    return "\n".join(lines)

def write_report(markdown: str, config: dict[str, Any]) -> Path:
    report_dir = Path(config["paths"]["report_dir"]).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"{dt.date.today():%Y-%m-%d}-etf-report.md"
    target.write_text(markdown, encoding="utf-8")
    log(f"写入报告: {target}")
    return target
