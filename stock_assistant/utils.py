import datetime as dt
import math
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from .models import Holding, Bar

def log(message: str) -> None:
    now = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", file=sys.stderr, flush=True)

def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged

def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value

def split_csv_setting(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]

def normalize_header(value: str) -> str:
    return re.sub(r"[\s_（）()%()]+", "", value.strip().lower())

def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "-", "nan", "None"}:
        return None
    text = text.replace(",", "").replace("，", "").replace("%", "")
    text = text.replace("元", "").replace("份", "").replace("股", "")
    try:
        return float(text)
    except ValueError:
        return None

def extract_code(value: Any) -> str:
    if value is None:
        return ""
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value))
    return match.group(1) if match else str(value).strip().upper()

def pick_value(row: dict[str, str], aliases: str | list[str]) -> str:
    normalized_row = {normalize_header(key): value for key, value in row.items()}
    for alias in split_csv_setting(aliases):
        hit = normalized_row.get(normalize_header(alias))
        if hit not in (None, ""):
            return hit
    return ""

def fmt(value: float | None, suffix: str = "", digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "-"
    return f"{value:.{digits}f}{suffix}"

def get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def compact_result_for_llm(item: dict[str, Any]) -> dict[str, Any]:
    holding = item["holding"]
    latest = item.get("latest")
    return {
        "code": get_attr(holding, "code"),
        "name": get_attr(holding, "name"),
        "quantity": get_attr(holding, "quantity"),
        "cost_price": get_attr(holding, "cost_price"),
        "market_value": get_attr(holding, "market_value"),
        "latest_date": str(get_attr(latest, "date")) if latest else None,
        "latest_close": get_attr(latest, "close"),
        "daily_pct_change": get_attr(latest, "pct_change"),
        "ma20": item.get("ma20"),
        "ma60": item.get("ma60"),
        "ma120": item.get("ma120"),
        "ret5_pct": item.get("ret5"),
        "ret20_pct": item.get("ret20"),
        "rsi14": item.get("rsi14"),
        "drawdown_from_120d_high_pct": item.get("drawdown"),
        "volatility20_pct": item.get("vol20"),
        "volume_ratio": item.get("vol_ratio"),
        "profit_pct": item.get("profit_pct"),
        "portfolio_weight_pct": item.get("weight"),
        "rule_action": item.get("action"),
        "rule_reason": item.get("reason"),
    }

def config_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def read_text_if_path(value: str, root_path: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root_path / path
    return path.read_text(encoding="utf-8").strip()
