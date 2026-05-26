"""CLI 入口"""

import argparse
import sys
from pathlib import Path

from stock_agent.config import load_config, get_mcp_url, get_mcp_token
from stock_agent.mcp_client import McpClient
from stock_agent.llm import create_llm
from stock_agent.graph import compile_graph


def load_env(env_path: Path = Path(".env")) -> None:
    """简易 .env 加载"""
    import os
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stock Agent - LangGraph 投资诊断")
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    parser.add_argument("--profile", default=None, help="LLM model profile 名称")
    parser.add_argument("--mcp-url", default=None, help="MCP 服务地址 (覆盖配置)")
    parser.add_argument("--mcp-token", default=None, help="MCP Bearer Token (覆盖配置)")
    args = parser.parse_args(argv)

    # 加载环境变量
    load_env()

    # 加载配置
    config_path = Path(args.config)
    if not config_path.exists():
        # 尝试从项目根目录找
        root_config = Path(__file__).parent.parent.parent.parent / "config.toml"
        if root_config.exists():
            config_path = root_config
        else:
            print(f"❌ 配置文件不存在: {config_path}", file=sys.stderr)
            return 1

    config = load_config(config_path)

    # 初始化 MCP 客户端
    mcp_url = args.mcp_url or get_mcp_url(config)
    mcp_token = args.mcp_token or get_mcp_token(config)
    mcp = McpClient(url=mcp_url, token=mcp_token)

    # 验证 MCP 连接
    print(f"🔗 连接 MCP: {mcp_url}")
    try:
        tools = mcp.list_tools()
        print(f"✅ MCP 连接成功，可用工具: {len(tools)} 个")
    except Exception as e:
        print(f"❌ MCP 连接失败: {e}", file=sys.stderr)
        return 1

    # 初始化 LLM
    print(f"🤖 初始化 LLM...")
    llm = create_llm(config, profile=args.profile)

    # 编译并运行图
    print(f"📊 开始投资诊断...\n")
    app = compile_graph(mcp, llm)

    # 执行
    initial_state = {
        "messages": [],
        "holdings": [],
        "portfolio_profile": {},
        "classifications": {},
        "anomalies": [],
        "investigations": [],
        "report": "",
        "phase": "init",
    }

    result = app.invoke(initial_state)

    # 输出报告
    report = result.get("report", "")
    if report:
        print("\n" + "=" * 60)
        print("📋 投资诊断报告")
        print("=" * 60)
        print(report)
        print("=" * 60)
    else:
        print("⚠️ 未生成报告")

    return 0


if __name__ == "__main__":
    sys.exit(main())
