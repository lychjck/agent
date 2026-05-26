"""配置加载"""

import os
from pathlib import Path
from typing import Any

import toml


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """加载 config.toml 配置"""
    if config_path is None:
        # 默认从项目根目录加载
        config_path = Path(__file__).parent.parent.parent.parent / "config.toml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    config = toml.load(config_path)
    return config


def get_llm_config(config: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    """获取 LLM 配置，支持 model_profiles 切换"""
    llm_cfg = config.get("llm", {})
    
    if profile and profile in llm_cfg.get("model_profiles", {}):
        # 合并 profile 配置到基础配置
        base = {k: v for k, v in llm_cfg.items() if k != "model_profiles"}
        base.update(llm_cfg["model_profiles"][profile])
        return base
    
    return {k: v for k, v in llm_cfg.items() if k != "model_profiles"}


def get_mcp_url(config: dict[str, Any]) -> str:
    """获取 MCP 服务地址"""
    server_cfg = config.get("server", {})
    host = server_cfg.get("http_host", "127.0.0.1")
    port = server_cfg.get("http_port", 8766)
    path = server_cfg.get("http_path", "/mcp")
    return f"http://{host}:{port}{path}"


def get_mcp_token(config: dict[str, Any]) -> str:
    """获取 MCP Bearer Token"""
    server_cfg = config.get("server", {})
    token_env = server_cfg.get("auth_token_env", "STOCK_MCP_TOKEN")
    return os.environ.get(token_env, "").strip()
