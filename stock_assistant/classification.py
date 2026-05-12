import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

from .models import Holding, InstrumentClassification
from .search import suggest_classification_with_search

def classification_from_config(holding: Holding, config: dict[str, Any]) -> InstrumentClassification | None:
    record = config.get("classifications", {}).get(holding.code)
    if not isinstance(record, dict):
        return None
    return InstrumentClassification(
        code=holding.code,
        name=holding.name,
        asset_class=str(record.get("asset_class", "unknown")),
        sector=str(record.get("sector", "")),
        theme=str(record.get("theme", "")),
        region=str(record.get("region", "unknown")),
        strategy=str(record.get("strategy", "unknown")),
        tracked_index=str(record.get("tracked_index", "")),
        issuer=str(record.get("issuer", "")),
        confidence=1.0,
        source="config",
        reviewed_by_user=True,
    )

def research_cache_path(code: str, config: dict[str, Any]) -> Path:
    cache_dir = Path(config.get("search", {}).get("cache_dir", "data/research")).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{code}.json"

def classification_cache_is_fresh(record: dict[str, Any], ttl_days: int) -> bool:
    if not isinstance(record, dict) or "retrieved_at" not in record:
        return False
    try:
        retrieved_at = dt.datetime.fromisoformat(record["retrieved_at"])
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - retrieved_at).days <= ttl_days
    except ValueError:
        return False

def classification_cache_has_search_content(record: dict[str, Any]) -> bool:
    source = str(record.get("source", ""))
    if not source.startswith("search"):
        return True
    evidence = record.get("evidence", [])
    if not isinstance(evidence, list):
        return False
    return any(
        isinstance(item, dict) and (item.get("snippet") or item.get("content") or item.get("raw_content"))
        for item in evidence
    )

def load_cached_classification(holding: Holding, config: dict[str, Any]) -> InstrumentClassification | None:
    path = research_cache_path(holding.code, config)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    
    ttl_days = int(config.get("classification", {}).get("cache_ttl_days", 90))
    if not classification_cache_is_fresh(record, ttl_days) and not record.get("reviewed_by_user"):
        return None
    if not record.get("reviewed_by_user") and str(record.get("source", "")).startswith("search"):
        min_confidence = float(config.get("classification", {}).get("require_user_review_below_confidence", 0.0))
        if float(record.get("confidence", 0.0)) < min_confidence:
            return None
        if not classification_cache_has_search_content(record):
            return None
    
    return InstrumentClassification(
        code=holding.code,
        name=holding.name,
        asset_class=str(record.get("asset_class", "unknown")),
        sector=str(record.get("sector", "")),
        theme=str(record.get("theme", "")),
        region=str(record.get("region", "unknown")),
        strategy=str(record.get("strategy", "unknown")),
        tracked_index=str(record.get("tracked_index", "")),
        issuer=str(record.get("issuer", "")),
        confidence=float(record.get("confidence", 0.0)),
        source=str(record.get("source", "cache")),
        reviewed_by_user=bool(record.get("reviewed_by_user", False)),
    )

def save_classification_cache(classification: InstrumentClassification, config: dict[str, Any]) -> Path:
    path = research_cache_path(classification.code, config)
    record = dataclasses.asdict(classification)
    record["retrieved_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

def local_heuristic_fallback(holding: Holding) -> InstrumentClassification | None:
    if "证券" in holding.name:
        return InstrumentClassification(
            code=holding.code,
            name=holding.name,
            asset_class="sector_equity",
            sector="financials",
            theme="brokerage",
            region="china_a",
            confidence=0.3,
            source="local_heuristic",
        )
    return None

def classify_holding(holding: Holding, config: dict[str, Any]) -> InstrumentClassification:
    return (
        classification_from_config(holding, config)
        or load_cached_classification(holding, config)
        or suggest_classification_with_search(holding, config)
        or local_heuristic_fallback(holding)
        or InstrumentClassification(code=holding.code, name=holding.name, source="unknown")
    )
