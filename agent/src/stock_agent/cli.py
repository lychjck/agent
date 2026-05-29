"""CLI 入口"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from stock_agent.config import get_mcp_token, get_mcp_url, load_config
from stock_agent.graph import compile_graph
from stock_agent.llm import create_llm
from stock_agent.mcp_client import McpClient


def _load_env(env_path: Path = Path(".env")) -> None:
    """简易 .env 加载，不覆盖已有环境变量"""
    import os
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


# ---------- 可观测性：给 MCP 客户端加日志 ----------

class ObservableMcpClient(McpClient):
    """包装 McpClient，打印每次工具调用的名称和耗时"""

    def __init__(self, url: str, token: str = "", timeout: float = 30.0, verbose: bool = True):
        super().__init__(url=url, token=token, timeout=timeout)
        self.verbose = verbose

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.verbose:
            args_brief = ""
            if arguments:
                args_brief = json.dumps(arguments, ensure_ascii=False)
                if len(args_brief) > 120:
                    args_brief = args_brief[:117] + "..."
            print(f"    🔧 {name}({args_brief})", flush=True)

        t0 = time.time()
        result = super().call_tool(name, arguments)
        elapsed = time.time() - t0

        if self.verbose:
            ok = result.get("ok", False)
            marker = "✓" if ok else "✗"
            detail = ""
            if not ok:
                detail = f" → {result.get('error_type', '')}: {result.get('message', '')[:80]}"
            else:
                # 简要展示返回数据量
                if "holdings" in result:
                    detail = f" → {result.get('count', '?')} 条持仓"
                elif "results" in result and isinstance(result["results"], dict):
                    detail = f" → {len(result['results'])} 个标的"
                elif "results" in result and isinstance(result["results"], list):
                    detail = f" → {len(result['results'])} 条结果"
                elif "portfolio" in result:
                    tv = result.get("portfolio", {}).get("total_value", 0)
                    detail = f" → 总市值 ¥{tv:,.0f}"
            print(f"    [{marker}] {elapsed:.1f}s{detail}", flush=True)

        return result


# ---------- 主入口 ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stock Agent - LangGraph 投资诊断")
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    parser.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    parser.add_argument("--quiet", action="store_true", help="静默模式，只输出最终报告")
    args = parser.parse_args(argv)

    _load_env()

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"❌ 配置加载失败: {exc}", file=sys.stderr)
        return 1

    mcp_url = get_mcp_url(config)
    mcp_token = get_mcp_token(config)
    verbose = not args.quiet
    mcp = ObservableMcpClient(url=mcp_url, token=mcp_token, verbose=verbose)

    if verbose:
        print(f"🔗 连接 MCP: {mcp_url}")
    try:
        tools = mcp.list_tools()
        if verbose:
            print(f"✅ MCP 已连接，工具数: {len(tools)}")
    except Exception as exc:
        print(f"❌ MCP 连接失败: {exc}", file=sys.stderr)
        return 1

    if verbose:
        print("🤖 初始化 LLM...")
    llm = create_llm(config)
    if verbose:
        llm_cfg = config.get("llm", {})
        profile = llm_cfg.get("default_profile", "")
        model = llm_cfg.get("model", "?")
        if profile and profile in llm_cfg.get("model_profiles", {}):
            model = llm_cfg["model_profiles"][profile].get("model", model)
        print(f"   模型: {model}")

    if verbose:
        print("\n📊 开始诊断...\n")

    app = compile_graph(mcp, llm)
    initial_state: dict = {"messages": []}
    t_start = time.time()

    if args.no_stream:
        result = app.invoke(initial_state)
    else:
        # stream_mode="updates" 每步给出 {node_name: node_output}
        result: dict = {}
        for chunk in app.stream(initial_state, stream_mode="updates"):
            for node_name, node_output in chunk.items():
                if node_name in ("__start__",):
                    continue

                if verbose:
                    # 节点开始标记
                    marker = "✓"
                    if isinstance(node_output, dict) and node_output.get("errors"):
                        marker = "✗"
                    print(f"\n  ── {marker} 节点: {node_name} ──", flush=True)

                    # 展示节点产出的关键信息
                    if isinstance(node_output, dict):
                        _print_node_summary(node_name, node_output)

                # 累积到最终 result
                if isinstance(node_output, dict):
                    for k, v in node_output.items():
                        if k == "messages":
                            result.setdefault("messages", []).extend(v if isinstance(v, list) else [v])
                        elif k in ("investigations", "errors") and isinstance(v, list):
                            result.setdefault(k, []).extend(v)
                        else:
                            result[k] = v

    elapsed = time.time() - t_start
    if verbose:
        print(f"\n⏱️  总耗时: {elapsed:.1f}s")

    report = result.get("report", "")
    if report:
        print("\n" + "=" * 60)
        print(report)
        print("=" * 60)
    else:
        errors = result.get("errors", [])
        if errors:
            print("\n⚠️ 节点错误：")
            for err in errors:
                print(f"  - {err.get('node')}: {err.get('message')}")
        else:
            print("\n⚠️ 未生成报告")

    return 0


def _print_node_summary(node_name: str, output: dict[str, Any]) -> None:
    """打印节点产出的关键摘要"""
    if node_name == "diagnose":
        holdings = output.get("holdings", [])
        anomalies = output.get("anomalies", [])
        tech = output.get("technical_data", {})
        print(f"    持仓: {len(holdings)} 只 | 技术指标: {len(tech)} 只 | 异常: {len(anomalies)} 个")
        if anomalies:
            for a in anomalies[:5]:
                print(f"      ⚠ {a.get('name', a.get('code'))}: {a.get('reason', '')[:60]}")
            if len(anomalies) > 5:
                print(f"      ... 还有 {len(anomalies) - 5} 个")

    elif node_name == "investigate":
        msgs = output.get("messages", [])
        if msgs:
            content = msgs[0].content if hasattr(msgs[0], "content") else str(msgs[0])
            print(f"    {content}")

    elif node_name in ("research_holding", "research_theme"):
        invs = output.get("investigations", [])
        for inv in invs:
            if inv.get("type") == "holding_research":
                news_count = len(inv.get("news") or [])
                const_count = len(inv.get("constituents") or [])
                print(f"    📈 {inv.get('name', inv.get('code'))} → 新闻:{news_count} 成分股:{const_count}")
            elif inv.get("type") == "theme_research":
                news_count = len(inv.get("news") or [])
                print(f"    🏷️  {inv.get('theme')} → 新闻:{news_count}")

    elif node_name == "report":
        rd = output.get("report_data", {})
        summary = rd.get("summary", {})
        score = summary.get("health_score", "?")
        status = summary.get("status", "?")
        ha_count = len(rd.get("holding_analysis", []))
        print(f"    LLM 报告: 健康分={score}, 状态={status}, 单标的建议={ha_count} 条")

    elif node_name == "render":
        report = output.get("report", "")
        print(f"    Markdown 渲染完成: {len(report)} 字符")

    elif node_name == "error_handler":
        errors = output.get("errors", []) or []
        print(f"    兜底报告生成（{len(errors)} 个错误）")


if __name__ == "__main__":
    sys.exit(main())
