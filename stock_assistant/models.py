import dataclasses
import datetime as dt
from typing import Any

@dataclasses.dataclass(frozen=True)
class Holding:
    code: str
    name: str
    quantity: float | None = None
    cost_price: float | None = None
    market_value: float | None = None
    profit_pct: float | None = None
    hold_profit: float | None = None
    day_profit: float | None = None
    source_row: dict[str, str] = dataclasses.field(default_factory=dict)
    asset_type: str = "etf"

@dataclasses.dataclass(frozen=True)
class Bar:
    date: dt.date
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    pct_change: float

@dataclasses.dataclass(frozen=True)
class InstrumentClassification:
    code: str
    name: str
    asset_class: str = "unknown"
    sector: str = ""
    theme: str = ""
    region: str = "unknown"
    strategy: str = "unknown"
    tracked_index: str = ""
    issuer: str = ""
    confidence: float = 0.0
    source: str = "unknown"
    evidence: tuple[dict[str, str], ...] = ()
    reviewed_by_user: bool = False

def holding_to_dict(holding: Holding) -> dict[str, Any]:
    return {
        "code": holding.code,
        "name": holding.name,
        "quantity": holding.quantity,
        "cost_price": holding.cost_price,
        "market_value": holding.market_value,
        "profit_pct": holding.profit_pct,
        "hold_profit": holding.hold_profit,
        "day_profit": holding.day_profit,
        "asset_type": holding.asset_type,
    }

def bar_to_dict(bar: Bar | None) -> dict[str, Any] | None:
    if bar is None:
        return None
    return {
        "date": str(bar.date),
        "open": bar.open,
        "close": bar.close,
        "high": bar.high,
        "low": bar.low,
        "volume": bar.volume,
        "amount": bar.amount,
        "pct_change": bar.pct_change,
    }

def analysis_result_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    output = dict(item)
    holding = output.get("holding")
    if isinstance(holding, Holding):
        output["holding"] = holding_to_dict(holding)
    latest = output.get("latest")
    if isinstance(latest, Bar):
        output["latest"] = bar_to_dict(latest)
    return output
