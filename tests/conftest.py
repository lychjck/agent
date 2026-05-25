import tomllib
from pathlib import Path

# 在测试开始前，注入单元测试专用的 MOCK_DEFAULTS 字典
# 物理读取 config.example.toml，并动态微调以完美契合旧测试环境

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG_PATH = ROOT / "config.example.toml"

MOCK_DEFAULTS = {}
if EXAMPLE_CONFIG_PATH.exists():
    with EXAMPLE_CONFIG_PATH.open("rb") as fh:
        MOCK_DEFAULTS = tomllib.load(fh)

# 核心兼容性微调：
# 1. 关闭自动网页正文读取以确保单元测试中 subprocess.run 仅被调用一次（测试假定行为）
if "search" in MOCK_DEFAULTS:
    MOCK_DEFAULTS["search"]["auto_read_top_result"] = False

# 2. 确保包含强校验所需的 mcp 和 server 基础小节
if "mcp" not in MOCK_DEFAULTS:
    MOCK_DEFAULTS["mcp"] = {"max_observation_chars": 12000}
if "server" not in MOCK_DEFAULTS:
    MOCK_DEFAULTS["server"] = {
        "default_transport": "stdio",
        "http_host": "127.0.0.1",
        "http_port": 8766,
        "http_path": "/mcp",
        "auth_token": "",
        "allow_unauthenticated": True,
    }

import stock_assistant.core.config as config_module
config_module.DEFAULTS = MOCK_DEFAULTS

import stock_assistant
stock_assistant.DEFAULTS = MOCK_DEFAULTS
