import tomllib
from pathlib import Path
from typing import Any

from stock_assistant.core.utils import config_bool, load_env_file, log

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.toml"

def load_config(path: Path) -> dict[str, Any]:
    load_env_file(ROOT / ".env")
    if not path.exists():
        raise FileNotFoundError(
            f"未找到配置文件 '{path}'！请复制 config.example.toml 并重命名为 config.toml 进行配置。"
        )
    
    log(f"读取配置: {path}")
    try:
        with path.open("rb") as fh:
            config = tomllib.load(fh)
    except Exception as e:
        raise ValueError(f"解析 TOML 配置文件 '{path}' 失败: {e}")

    # --- 强行参数与类型校验 ---
    required_specs = [
        # (section, key, expected_types)
        ("paths", "archive_dir", str),
        ("paths", "report_dir", str),
        ("paths", "download_dir", str),
        ("ledger", "mode", str),
        ("market", "provider", str),
        ("analysis", "loss_alert_pct", (int, float)),
        ("analysis", "max_single_position_pct", (int, float)),
        ("analysis", "min_history_days", int),
        ("policy", "cash_min_pct", (int, float)),
        ("policy", "max_single_position_pct", (int, float)),
        ("policy", "max_sector_pct", (int, float)),
        ("policy", "max_theme_pct", (int, float)),
        ("policy", "max_unknown_classification_pct", (int, float)),
        ("policy", "loss_alert_pct", (int, float)),
        ("policy", "gain_trim_pct", (int, float)),
        ("llm", "base_url", str),
        ("llm", "model", str),
        ("mcp", "max_observation_chars", int),
        ("server", "default_transport", str),
        ("server", "http_host", str),
        ("server", "http_port", int),
        ("server", "http_path", str),
        ("server", "auth_token", str),
        ("server", "allow_unauthenticated", bool),
    ]

    missing_errors = []
    for section, key, expected_types in required_specs:
        if section not in config or not isinstance(config[section], dict):
            missing_errors.append(f"缺失配置小节: [{section}]")
            continue
            
        val = config[section].get(key)
        if val is None:
            missing_errors.append(f"缺失必要配置项: [{section}] {key}")
        elif not isinstance(val, expected_types):
            expected_name = (
                " or ".join(t.__name__ for t in expected_types)
                if isinstance(expected_types, tuple)
                else expected_types.__name__
            )
            missing_errors.append(
                f"配置类型错误: [{section}] {key} (期望: {expected_name}, 实际: {type(val).__name__})"
            )
            
    if missing_errors:
        raise ValueError("配置文件检测失败，存在以下缺失或错误参数：\n" + "\n".join(f" - {e}" for e in missing_errors))

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

