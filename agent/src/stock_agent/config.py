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
    """获取 MCP 服务地址。

    优先读 [mcp_client] 段（agent 视角：连到哪），不存在时直接报错。
    [server] 段是 MCP 服务端自己的监听配置，agent 不应当读取。
    """
    cfg = config.get("mcp_client")
    if not isinstance(cfg, dict):
        raise RuntimeError(
            "config.toml 缺少 [mcp_client] 段，请配置 url，例如：\n"
            "[mcp_client]\nurl = \"http://127.0.0.1:9099/mcp\""
        )

    url = str(cfg.get("url", "")).strip()
    if url:
        return url

    # 拆分形式兼容：host + port + path
    host = cfg.get("host")
    port = cfg.get("port")
    path = cfg.get("path", "/mcp")
    if host and port:
        return f"http://{host}:{port}{path}"

    raise RuntimeError(
        "[mcp_client] 必须提供 url，或同时提供 host + port，例如：\n"
        "[mcp_client]\nurl = \"http://127.0.0.1:9099/mcp\""
    )


def get_mcp_token(config: dict[str, Any]) -> str:
    """获取 MCP Bearer Token

    支持两种来源（按优先级）：
    1. [mcp_client] token = "..."  直接写明文（不推荐）
    2. [mcp_client] token_env = "STOCK_MCP_TOKEN"  从环境变量读取
    """
    cfg = config.get("mcp_client", {})
    if not isinstance(cfg, dict):
        return ""

    token = str(cfg.get("token", "")).strip()
    if token:
        return token

    token_env = str(cfg.get("token_env", "")).strip()
    if token_env:
        return os.environ.get(token_env, "").strip()
    return ""
