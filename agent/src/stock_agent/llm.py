"""LLM 客户端封装"""

import os
from typing import Any

from langchain_openai import ChatOpenAI

from stock_agent.config import get_llm_config


def create_llm(config: dict[str, Any]) -> ChatOpenAI:
    """根据配置文件创建 LLM 实例。

    profile 从 [llm] 段的 default_profile 字段读取，不接受命令行传参。
    """
    llm_cfg = get_llm_config(config)

    # 获取 API key
    api_key = ""
    api_key_env = llm_cfg.get("api_key_env", "")
    if api_key_env:
        api_key = os.environ.get(api_key_env, "")
    if not api_key:
        api_key = "not-needed"  # 本地模型不需要 key

    return ChatOpenAI(
        model=llm_cfg.get("model", "gpt-4"),
        base_url=llm_cfg.get("base_url", ""),
        api_key=api_key,
        temperature=llm_cfg.get("temperature", 0.2),
        max_tokens=llm_cfg.get("max_tokens", 8192),
        streaming=llm_cfg.get("stream", False),
    )
