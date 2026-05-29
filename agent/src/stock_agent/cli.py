"""CLI 入口"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stock Agent - LangGraph 投资诊断")
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    parser.add_argument("--no-stream", action="store_true", help="关闭节点级流式输出")
    args = parser.parse_args(argv)

    _load_env()

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"❌ 配置加载失败: {exc}", file=sys.stderr)
        return 1

    mcp_url = get_mcp_url(config)
    mcp_token = get_mcp_token(config)
    mcp = McpClient(url=mcp_url, token=mcp_token)

    print(f"🔗 连接 MCP: {mcp_url}")
    try:
        tools = mcp.list_tools()
        print(f"✅ MCP 已连接，工具数: {len(tools)}")
    except Exception as exc:
        print(f"❌ MCP 连接失败: {exc}", file=sys.stderr)
        return 1

    print("🤖 初始化 LLM...")
    llm = create_llm(config)

    print("📊 开始诊断...\n")
    app = compile_graph(mcp, llm)

    initial_state: dict = {"messages": []}

    if args.no_stream:
        result = app.invoke(initial_state)
    else:
        # stream_mode="values" 每步给完整 state 快照，最后一帧就是最终结果
        result: dict = {}
        seen_nodes: set[str] = set()
        for snapshot in app.stream(initial_state, stream_mode="values"):
            result = snapshot
            # 通过判断 state 里出现的新字段大致推断刚跑完哪个节点
            new_keys = set(snapshot.keys()) - seen_nodes
            for k in sorted(new_keys):
                if k in ("messages", "errors"):
                    continue
                seen_nodes.add(k)
                print(f"  [✓] state.{k} 已写入")
            if snapshot.get("errors"):
                print(f"  [✗] 出现 {len(snapshot['errors'])} 个节点错误")

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


if __name__ == "__main__":
    sys.exit(main())
