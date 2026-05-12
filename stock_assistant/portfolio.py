from typing import Any

from .models import Holding, InstrumentClassification


def value_map_to_pct(values: dict[str, float], total_value: float) -> dict[str, float]:
    if total_value <= 0:
        return {key: 0.0 for key in values}
    return {key: value / total_value * 100 for key, value in values.items()}


def add_value(bucket: dict[str, float], key: str, value: float) -> None:
    normalized_key = key if key else "unknown"
    bucket[normalized_key] = bucket.get(normalized_key, 0.0) + value


def sorted_pct_items(values: dict[str, float]) -> list[dict[str, Any]]:
    return [
        {"key": key, "pct": pct}
        for key, pct in sorted(values.items(), key=lambda item: item[1], reverse=True)
    ]


def summarize_portfolio(
    holdings: list[Holding],
    classifications: dict[str, InstrumentClassification],
    config: dict[str, Any],
) -> dict[str, Any]:
    total_value = sum(holding.market_value or 0.0 for holding in holdings)
    low_confidence_threshold = float(
        config.get("classification", {}).get("require_user_review_below_confidence", 0.75)
    )

    by_asset_class: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    by_theme: dict[str, float] = {}
    by_strategy: dict[str, float] = {}
    by_region: dict[str, float] = {}
    by_asset_type: dict[str, float] = {}
    positions: list[dict[str, Any]] = []
    unknown_value = 0.0
    low_confidence_value = 0.0

    for holding in holdings:
        value = holding.market_value or 0.0
        classification = classifications.get(holding.code)
        asset_class = classification.asset_class if classification else "unknown"
        sector = classification.sector if classification and classification.sector else "unknown"
        theme = classification.theme if classification and classification.theme else "unknown"
        strategy = classification.strategy if classification and classification.strategy else "unknown"
        region = classification.region if classification and classification.region else "unknown"
        confidence = classification.confidence if classification else 0.0
        asset_type = holding.asset_type or "unknown"

        add_value(by_asset_class, asset_class, value)
        add_value(by_sector, sector, value)
        add_value(by_theme, theme, value)
        add_value(by_strategy, strategy, value)
        add_value(by_region, region, value)
        add_value(by_asset_type, asset_type, value)

        if classification is None or asset_class == "unknown":
            unknown_value += value
        if classification is None or confidence < low_confidence_threshold:
            low_confidence_value += value

        positions.append({
            "code": holding.code,
            "name": holding.name,
            "market_value": value,
            "weight": value / total_value * 100 if total_value else None,
            "asset_class": asset_class,
            "sector": sector,
            "theme": theme,
            "strategy": strategy,
            "region": region,
            "asset_type": asset_type,
            "classification_confidence": confidence,
            "classification_source": classification.source if classification else "unknown",
            "reviewed_by_user": classification.reviewed_by_user if classification else False,
        })

    positions.sort(key=lambda item: item["market_value"], reverse=True)

    return {
        "total_value": total_value,
        "position_count": len(holdings),
        "by_asset_class": value_map_to_pct(by_asset_class, total_value),
        "by_sector": value_map_to_pct(by_sector, total_value),
        "by_theme": value_map_to_pct(by_theme, total_value),
        "by_strategy": value_map_to_pct(by_strategy, total_value),
        "by_region": value_map_to_pct(by_region, total_value),
        "by_asset_type": value_map_to_pct(by_asset_type, total_value),
        "top_positions": positions[:5],
        "positions": positions,
        "unknown_classification_pct": unknown_value / total_value * 100 if total_value else 0.0,
        "low_confidence_classification_pct": (
            low_confidence_value / total_value * 100 if total_value else 0.0
        ),
    }


def top_pct_items(values: dict[str, float], limit: int = 3, include_unknown: bool = False) -> list[dict[str, Any]]:
    items = [
        {"key": key, "pct": pct}
        for key, pct in values.items()
        if pct > 0 and (include_unknown or key != "unknown")
    ]
    return sorted(items, key=lambda item: item["pct"], reverse=True)[:limit]


def pct_evidence(key: str, pct: float) -> str:
    return f"{key}={pct:.2f}%"


def generate_portfolio_observations(summary: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    positions = summary.get("positions", [])

    if positions:
        largest = positions[0]
        weight = largest.get("weight")
        if weight is not None:
            observations.append({
                "id": f"observation:largest_position:{largest.get('code', 'unknown')}",
                "type": "largest_position",
                "label": "最大单只持仓",
                "evidence": [f"{largest.get('name', '')} weight={float(weight):.2f}%"],
            })

    for item in top_pct_items(summary.get("by_asset_class", {}), limit=limit):
        observations.append({
            "id": f"observation:top_asset_class:{item['key']}",
            "type": "top_asset_class",
            "label": "主要资产大类暴露",
            "evidence": [pct_evidence(item["key"], item["pct"])],
        })

    for item in top_pct_items(summary.get("by_sector", {}), limit=limit):
        observations.append({
            "id": f"observation:top_sector:{item['key']}",
            "type": "top_sector",
            "label": "主要行业暴露",
            "evidence": [pct_evidence(item["key"], item["pct"])],
        })

    for item in top_pct_items(summary.get("by_theme", {}), limit=limit):
        observations.append({
            "id": f"observation:top_theme:{item['key']}",
            "type": "top_theme",
            "label": "主要主题暴露",
            "evidence": [pct_evidence(item["key"], item["pct"])],
        })

    unknown_pct = float(summary.get("unknown_classification_pct", 0.0) or 0.0)
    if unknown_pct > 0:
        observations.append({
            "id": "observation:unknown_classification",
            "type": "unknown_classification",
            "label": "未知分类占比",
            "evidence": [f"unknown_classification={unknown_pct:.2f}%"],
        })

    low_confidence_pct = float(summary.get("low_confidence_classification_pct", 0.0) or 0.0)
    if low_confidence_pct > 0:
        observations.append({
            "id": "observation:low_confidence_classification",
            "type": "low_confidence_classification",
            "label": "低置信度分类占比",
            "evidence": [f"low_confidence_classification={low_confidence_pct:.2f}%"],
        })

    strategy = summary.get("by_strategy", {})
    passive_pct = float(strategy.get("passive_index", 0.0) or 0.0)
    active_pct = sum(
        float(strategy.get(key, 0.0) or 0.0)
        for key in ("active_management", "mixed_allocation")
    )
    if passive_pct > 0 or active_pct > 0:
        observations.append({
            "id": "observation:active_vs_passive",
            "type": "active_vs_passive",
            "label": "主动/被动占比",
            "evidence": [
                f"passive_index={passive_pct:.2f}%",
                f"active_or_mixed={active_pct:.2f}%",
            ],
        })

    asset_type = summary.get("by_asset_type", {})
    etf_pct = float(asset_type.get("etf", 0.0) or 0.0)
    fund_pct = float(asset_type.get("fund", 0.0) or 0.0)
    if etf_pct > 0 or fund_pct > 0:
        observations.append({
            "id": "observation:on_exchange_vs_off_exchange",
            "type": "on_exchange_vs_off_exchange",
            "label": "场内/场外占比",
            "evidence": [f"etf={etf_pct:.2f}%", f"fund={fund_pct:.2f}%"],
        })

    return observations
