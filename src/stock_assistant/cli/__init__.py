from pathlib import Path
from typing import Any

from .cli import main, run


def build_portfolio_profile(
    config: dict[str, Any],
    holdings_file: str | Path | None = None,
    refresh_classification: bool = False,
) -> dict[str, Any]:
    import stock_assistant as sa

    if holdings_file is None:
        holdings, source, ledger_summary = sa.fetch_tzzb_holdings(config)
    else:
        source = sa.archive_holding_file(Path(holdings_file), config)
        holdings = sa.parse_holdings(source, config)
        ledger_summary = {}

    classifications = {}
    for holding in holdings:
        if refresh_classification:
            classification = sa.classify_holding(holding, config)
        else:
            classification = (
                sa.classification_from_config(holding, config)
                or sa.load_cached_classification(holding, config)
                or sa.InstrumentClassification(code=holding.code, name=holding.name, source="unknown")
            )
        classifications[holding.code] = classification

    summary = sa.summarize_portfolio(holdings, classifications, config)
    observations = sa.generate_portfolio_observations(summary)
    return {
        "source": str(source) if source is not None else None,
        "ledger_summary": ledger_summary,
        "summary": summary,
        "observations": observations,
        "classifications": {
            code: {
                "code": classification.code,
                "name": classification.name,
                "asset_class": classification.asset_class,
                "sector": classification.sector,
                "theme": classification.theme,
                "region": classification.region,
                "strategy": classification.strategy,
                "tracked_index": classification.tracked_index,
                "issuer": classification.issuer,
                "confidence": classification.confidence,
                "source": classification.source,
                "reviewed_by_user": classification.reviewed_by_user,
            }
            for code, classification in classifications.items()
        },
    }


__all__ = ["build_portfolio_profile", "main", "run"]
