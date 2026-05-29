"""配置加载"""

import os
from pathlib import Path
from typing import Any

import toml


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """加载 config.toml 配置，如果找不到则自适应逐级向上寻找"""
    target_path = None
    
    if config_path:
        path = Path(config_path)
        if path.is_absolute() or path.exists():
            target_path = path
        else:
            target_name = path.name
    else:
        target_name = "config.toml"

    # 如果还没确定路径，采用逐级向上寻找机制
    if target_path is None:
        # 1. 尝试从当前文件所在位置逐级向上寻找
        curr = Path(__file__).resolve().parent
        for parent in curr.parents:
            candidate = parent / target_name
            if candidate.exists():
                target_path = candidate
                break

    if target_path is None:
        # 2. 尝试从当前工作目录寻找
        candidate = Path.cwd() / target_name
        if candidate.exists():
            target_path = candidate

    if target_path is None or not target_path.exists():
        raise FileNotFoundError(f"配置文件 '{target_name}' 未找到，已尝试在各级父目录及当前工作目录下搜寻。")

    return toml.load(target_path)


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
