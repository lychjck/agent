import argparse
import os
import sys
from pathlib import Path

from stock_mcp.core import logger, load_config
from stock_mcp.server import McpServer, serve_stdio, create_http_app
import uvicorn


def load_env(env_path: Path = Path(".env")) -> None:
    """极简加载 .env 文件注入到 os.environ，不覆盖已有环境变量"""
    if env_path.exists():
        try:
            with env_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip("'\"")
                        if key and key not in os.environ:
                            os.environ[key] = val
            logger.info(f"Loaded environment variables from {env_path}")
        except Exception as e:
            logger.warning(f"Failed to load .env: {e}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run stock assistant MCP server.")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    return parser


def main(argv: list[str] | None = None) -> int:
    # 0. 优先加载本地环境变量
    load_env()

    args = build_arg_parser().parse_args(argv)

    # 1. 加载并强校验配置（文件缺失/参数缺失直接抛错）
    config_path = Path(args.config)
    config = load_config(config_path)

    server_cfg = config.get("server", {})
    transport = str(server_cfg.get("default_transport", "stdio")).strip()

    # 2. 实例化服务
    server = McpServer(config)

    # 3. 根据 config.toml [server] default_transport 调度
    if transport == "stdio":
        logger.info("Starting stock_mcp server over Stdio transport")
        try:
            serve_stdio(server)
        except KeyboardInterrupt:
            pass
        return 0

    # HTTP transport
    host = str(server_cfg.get("http_host", "127.0.0.1"))
    port = int(server_cfg.get("http_port", 8766))
    path = str(server_cfg.get("http_path", "/mcp"))
    auth_token_env = str(server_cfg.get("auth_token_env", "STOCK_MCP_TOKEN"))
    auth_token = str(os.environ.get(auth_token_env, "")).strip()

    if not auth_token:
        sys.stderr.write(
            f"HTTP MCP server 需要在环境变量 {auth_token_env} 中设置 Bearer Token。\n"
        )
        return 2

    app = create_http_app(server, auth_token=auth_token, mcp_path=path)
    logger.info(f"Starting stock_mcp server over HTTP at http://{host}:{port}{path}")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())


