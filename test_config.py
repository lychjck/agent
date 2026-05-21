import tomllib
from pathlib import Path
from typing import Any

from stock_assistant.core.utils import config_bool, load_env_file, log

ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"

_EXAMPLE_CONFIG_PATH = ROOT / "config.example.toml"

DEFAULTS: dict[str, Any] = {}
if _EXAMPLE_CONFIG_PATH.exists():
    with _EXAMPLE_CONFIG_PATH.open("rb") as fh:
        DEFAULTS = tomllib.load(fh)
else:
    DEFAULTS = {
        "paths": {
            "download_dir": "downloads",
            "report_dir": "reports",
            "archive_dir": "data/holdings",
        }
    }

def load_config(path: Path) -> dict[str, Any]:
    load_env_file(ROOT / ".env")
    config = DEFAULTS
    if path.exists():
        log(f"读取配置: {path}")
        with path.open("rb") as fh:
            config = tomllib.load(fh)
    else:
        log(f"未找到配置文件: {path}，使用 config.example.toml 的默认配置。")
    return config

def ensure_dirs(config: dict[str, Any]) -> None:
    for key in ("download_dir", "report_dir", "archive_dir"):
        val = config.get("paths", {}).get(key)
        if val:
            (ROOT / str(val)).expanduser().mkdir(parents=True, exist_ok=True)
    if config_bool(config.get("skills", {}).get("enabled", True)):
        install_dir = config.get("skills", {}).get("install_dir", "data/skills")
        (ROOT / str(install_dir)).expanduser().mkdir(parents=True, exist_ok=True)

def policy_value(config: dict[str, Any], key: str, fallback: Any = None) -> Any:
    if key in config.get("policy", {}):
        return config["policy"][key]
    if key in config.get("analysis", {}):
        return config["analysis"][key]
    return fallback
