from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

from stock_assistant.agents.agent_loop import run_tool_agent_events
from stock_assistant.core.config import DEFAULT_CONFIG, ensure_dirs, load_config
from stock_assistant.core.skills import list_installed_skills


DEFAULT_MODEL = "google/gemma-4-26b-a4b"


def build_goal(today: str, query: str) -> str:
    return (
        f"今天是 {today}（Asia/Shanghai）。这是一次 multi-search-engine skill 能力 smoke test。\n"
        "任务：搜索今天股市行情并做简要分析，重点覆盖 A 股主要指数、港股和美股期指/上一交易日美股表现；"
        "如果搜索结果无法覆盖某个市场，必须在 limitations 中说明。\n"
        "执行要求：\n"
        "1. 必须先调用 list_skills 确认 multi-search-engine 已安装。\n"
        "2. 必须调用 read_skill 读取 multi-search-engine 的 SKILL.md。\n"
        "3. 必须调用 list_skill_files，并最多调用一次 read_skill_file；中文行情优先读取 references/advanced-search.md，"
        "不要为了完整学习而读取全部 references。\n"
        "4. 必须按 skill 的方法选择合适搜索引擎；优先调用 web_search 搜索，再调用 web_read 打开具体结果页。"
        "只有需要直接访问已知 URL 时才调用 web_fetch。\n"
        "5. 本次是搜索能力测试，不要调用持仓读取、持仓技术分析、组合画像等账户相关工具。\n"
        "6. 只有 observation 文本中明确出现指数名称和具体点位/涨跌幅/涨跌额时，才算覆盖该市场；"
        "如果只读到门户首页、宏观新闻或没有数值，不得说已覆盖。\n"
        "7. 最终报告用中文，区分已从搜索结果验证的事实、基于事实的分析、仍缺失的信息。\n"
        f"搜索主题：{query}"
    )


def compact(value: Any, max_chars: int = 1200) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n...[truncated {len(text)} chars]"


def print_event(event: dict[str, Any]) -> None:
    step = event.get("step", "-")
    status = event.get("status", "")
    print(f"\n[{step}] {status}")

    if event.get("run_id"):
        print(f"run_id: {event['run_id']}")
    if event.get("turn"):
        print(f"turn: {event['turn']}")

    if step in {"research_plan", "llm_decision", "observation_reflection"}:
        if event.get("reasoning_summary"):
            print(f"reasoning: {event['reasoning_summary']}")
        parsed = event.get("parsed")
        if isinstance(parsed, dict):
            if parsed.get("tool_calls"):
                print("tool_calls:")
                print(compact(parsed["tool_calls"], max_chars=2000))
            if parsed.get("research_plan"):
                print("research_plan:")
                print(compact(parsed["research_plan"], max_chars=2000))
            if parsed.get("observation_reflection"):
                print("observation_reflection:")
                print(compact(parsed["observation_reflection"], max_chars=2000))

    if step == "tool_call":
        print(f"tool: {event.get('tool')}")
        print(f"arguments: {compact(event.get('arguments', {}), max_chars=2000)}")

    if step == "tool_observation":
        print(f"tool: {event.get('tool')}")
        print(f"ok: {event.get('ok')}")
        if event.get("summary"):
            print(f"summary: {event['summary']}")
        observation = event.get("observation")
        if isinstance(observation, dict):
            result = observation.get("result")
            if isinstance(result, dict):
                preview_keys = (
                    "summary",
                    "query",
                    "engines",
                    "count",
                    "results",
                    "url",
                    "final_url",
                    "content_type",
                    "content",
                    "truncated",
                    "preview",
                    "original_chars",
                )
                preview = {key: result.get(key) for key in preview_keys if key in result}
                print(compact(preview, max_chars=3000))

    if step == "final_report":
        print("final_report emitted")

    if step == "done":
        snapshot = event.get("snapshot")
        report = snapshot.get("agent_report") if isinstance(snapshot, dict) else None
        print("agent_report:")
        print(compact(report, max_chars=6000))

    if step == "error":
        print("error:")
        print(compact(event, max_chars=4000))


async def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    config = load_config(Path(args.config).expanduser())
    ensure_dirs(config)

    installed = {record.name for record in list_installed_skills(config)}
    if "multi-search-engine" not in installed:
        print("未安装 multi-search-engine，请先运行：", file=sys.stderr)
        print(
            "uv run stock-assistant --config config.toml skills install "
            "https://clawhub.ai/gpyangyoujun/multi-search-engine",
            file=sys.stderr,
        )
        return 2

    config["llm"]["enabled"] = True
    config["agent"]["tool_agent_enabled"] = True
    config["agent"]["allow_external_search_tools"] = True
    config["agent"]["max_tool_turns"] = args.max_turns
    config["agent"]["max_tool_calls"] = args.max_calls
    config["agent"]["save_traces"] = True

    today = args.date or dt.datetime.now(dt.UTC).astimezone().date().isoformat()
    goal = args.goal or build_goal(today, args.query)
    print(f"model: {args.model}")
    print(f"goal:\n{goal}")

    saw_error = False
    async for event in run_tool_agent_events(
        config,
        goal=goal,
        cached_results=[],
        model_override=args.model,
        save_snapshot=not args.no_save_snapshot,
        save_report=not args.no_save_report,
    ):
        print_event(event)
        if event.get("step") == "error":
            saw_error = True
            break
    return 1 if saw_error else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test multi-search-engine skill with the tool agent.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径，默认 ./config.toml")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="用于 tool-agent 的 LLM 模型")
    parser.add_argument("--query", default="今天股市行情 A股 港股 美股 指数", help="搜索主题")
    parser.add_argument("--date", default="", help="覆盖脚本传给 agent 的日期，默认使用本机今天")
    parser.add_argument("--goal", default="", help="覆盖完整 agent 目标")
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--max-calls", type=int, default=32)
    parser.add_argument("--no-save-snapshot", action="store_true", default=True)
    parser.add_argument("--no-save-report", action="store_true", default=True)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
