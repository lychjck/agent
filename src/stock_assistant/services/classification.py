import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

from stock_assistant.core.models import Holding, InstrumentClassification
from stock_assistant.integrations.search import suggest_classification_with_search
from stock_assistant.core.utils import log

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

def classification_cache_status(holding: Holding, config: dict[str, Any]) -> tuple[str, str]:
    path = research_cache_path(holding.code, config)
    if not path.exists():
        return "miss", f"cache file not found: {path}"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return "invalid_json", f"cannot read cache {path}: {exc}"

    ttl_days = int(config.get("classification", {}).get("cache_ttl_days", 90))
    reviewed = bool(record.get("reviewed_by_user", False))
    if not classification_cache_is_fresh(record, ttl_days) and not reviewed:
        return "stale", f"retrieved_at={record.get('retrieved_at', '')} ttl_days={ttl_days}"

    source = str(record.get("source", ""))
    if not reviewed and source.startswith("search"):
        confidence = float(record.get("confidence", 0.0))
        min_confidence = float(config.get("classification", {}).get("require_user_review_below_confidence", 0.0))
        if confidence < min_confidence:
            return "low_confidence", f"confidence={confidence:.4f} < threshold={min_confidence:.4f}"
        if not classification_cache_has_search_content(record):
            return "missing_evidence_content", "search cache has no snippet/content/raw_content"

    return "usable", f"source={source or 'cache'} confidence={float(record.get('confidence', 0.0)):.4f} reviewed={reviewed}"

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
    
    # 即使置信度低也返回，让 UI 或汇总逻辑决定如何显示（例如标记为低置信度）
    # 只有在没有证据内容且未通过审核时才视为无效缓存
    if not record.get("reviewed_by_user") and str(record.get("source", "")).startswith("search"):
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
    configured = classification_from_config(holding, config)
    if configured is not None:
        log(f"分类命中手动配置: {holding.name} ({holding.code}) source=config")
        return configured

    cached = load_cached_classification(holding, config)
    if cached is not None:
        log(f"分类命中缓存: {holding.name} ({holding.code}) source={cached.source} confidence={cached.confidence:.4f}")
        return cached

    cache_status, cache_detail = classification_cache_status(holding, config)
    log(f"分类缓存不可用: {holding.name} ({holding.code}) reason={cache_status}; {cache_detail}")

    searched = suggest_classification_with_search(holding, config, reason=cache_status)
    if searched is not None:
        log(f"分类搜索完成: {holding.name} ({holding.code}) source={searched.source} confidence={searched.confidence:.4f}")
        return searched

    fallback = local_heuristic_fallback(holding)
    if fallback is not None:
        log(f"分类使用本地兜底: {holding.name} ({holding.code}) source={fallback.source} confidence={fallback.confidence:.4f}")
        return fallback

    log(f"分类未知: {holding.name} ({holding.code})")
    return InstrumentClassification(code=holding.code, name=holding.name, source="unknown")
