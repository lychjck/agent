import tomllib
from pathlib import Path
from typing import Any

from stock_assistant.core.utils import config_bool, deep_merge, load_env_file, log

ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"

DEFAULTS: dict[str, Any] = {
    "paths": {
        "download_dir": str(ROOT / "downloads"),
        "report_dir": str(ROOT / "reports"),
        "archive_dir": str(ROOT / "data" / "holdings"),
    },
    "ledger": {
        "url": "",
        "mode": "tzzb_api",
        "curl_file": "",
        "cookie_file": "",
        "cookie": "",
        "uid": "",
        "api_timeout_seconds": 30,
    },
    "market": {
        "provider": "sina",
        "history_days": 260,
        "timeout_seconds": 15,
    },
    "analysis": {
        "min_history_days": 80,
        "loss_alert_pct": -8.0,
        "gain_trim_pct": 20.0,
        "max_single_position_pct": 35.0,
    },
    "profile": {
        "base_currency": "CNY",
        "risk_level": "balanced",
        "investment_style": "long_term_etf",
        "allow_external_search": False,
        "allow_external_llm": True,
    },
    "policy": {
        "cash_min_pct": 5,
        "max_single_position_pct": 20,
        "max_sector_pct": 35,
        "max_theme_pct": 25,
        "max_unknown_classification_pct": 10,
        "loss_alert_pct": -8,
        "gain_trim_pct": 20,
        "rebalance_drift_pct": 5,
        "rebalance_target_buffer_pct": 2,
    },
    "allocation_targets": {
        "broad_index": 40,
        "sector_equity": 25,
        "bond": 15,
        "overseas": 10,
        "commodity": 5,
        "cash": 5,
    },
    "classification": {
        "mode": "hybrid",
        "require_user_review_below_confidence": 0.75,
        "cache_ttl_days": 90,
        "llm": {
            "enabled": True,
            "client": "urllib",
            "base_url": "http://10.33.207.193:1234/v1",
            "model": "google/gemma-4-31b",
            "temperature": 0.0,
            "timeout_seconds": 120,
            "max_tokens": 2048,
            "stream": False,
            "disable_thinking": True,
        },
    },
    "classifications": {
        "510300": {
            "asset_class": "broad_index",
            "sector": "",
            "theme": "csi300",
            "region": "china_a",
            "strategy": "passive_index",
            "reviewed_by_user": True,
        }
    },
    "search": {
        "enabled": False,
        "provider": "manual_json",
        "cache_dir": str(ROOT / "data" / "research"),
        "timeout_seconds": 20,
        "max_results": 5,
        "search_depth": "basic",
        "freshness": "year",
        "start_date": "",
        "end_date": "",
        "include_raw_content": False,
        "max_stored_content_chars": 4000,
        "api_key_env": "",
        "manual_results_file": str(ROOT / "data" / "research" / "manual_search_results.json"),
        "providers": {
            "tavily": {
                "enabled": False,
                "api_key_env": "TAVILY_API_KEY",
                "search_depth": "basic",
                "topic": "finance",
            },
            "brave": {
                "enabled": False,
                "api_key_env": "BRAVE_SEARCH_API_KEY",
            }
        },
        "source_tiers": {
            "tier1": "sse.com.cn,szse.cn,csindex.com.cn,cnindex.com.cn",
            "tier2": "eastmoney.com,10jqka.com.cn,fund.eastmoney.com",
        }
    },
    "agent": {
        "enabled": True,
        "strict_json": True,
        "llm_can_create_new_actions": False,
        "save_snapshots": True,
        "snapshot_dir": str(ROOT / "data" / "state"),
        "tool_agent_enabled": True,
        "tool_agent_default": True,
        "max_tool_turns": 32,
        "max_tool_calls": 80,
        "save_traces": True,
        "trace_dir": str(ROOT / "data" / "state" / "agent_traces"),
        "allow_external_search_tools": False,
    },
    "skills": {
        "enabled": True,
        "install_dir": str(ROOT / "data" / "skills"),
        "roots": [],
        "max_skill_chars": 20000,
        "install_timeout_seconds": 30,
        "allow_url_install": True,
    },
    "llm": {
        "enabled": False,
        "client": "openai",
        "base_url": "http://10.33.207.193:1234/v1",
        "model": "google/gemma-4-26b-a4b",
        "api_key_env": "",
        "api_key_file": "",
        "temperature": 0.2,
        "timeout_seconds": 900,
        "max_tokens": 65536,
        "stream": True,
        "disable_thinking": False,
        "reasoning_effort": "",
        "structured_output": "auto",
        "supports_response_format": False,
        "repair_attempts": 1,
        "max_context_positions": 50,
        "log_payload": False,
    },
}

def load_config(path: Path) -> dict[str, Any]:
    load_env_file(ROOT / ".env")
    config = DEFAULTS
    if path.exists():
        log(f"读取配置: {path}")
        with path.open("rb") as fh:
            config = deep_merge(DEFAULTS, tomllib.load(fh))
    else:
        log(f"未找到配置文件: {path}，使用内置默认配置。")
    return config

def ensure_dirs(config: dict[str, Any]) -> None:
    for key in ("download_dir", "report_dir", "archive_dir"):
        Path(config["paths"][key]).expanduser().mkdir(parents=True, exist_ok=True)
    if config_bool(config.get("skills", {}).get("enabled", True)):
        Path(config.get("skills", {}).get("install_dir", ROOT / "data" / "skills")).expanduser().mkdir(
            parents=True,
            exist_ok=True,
        )

def policy_value(config: dict[str, Any], key: str, fallback: Any = None) -> Any:
    if key in config.get("policy", {}):
        return config["policy"][key]
    if key in config.get("analysis", {}):
        return config["analysis"][key]
    return fallback
