import statistics
from typing import Any

from stock_assistant.services.market import fetch_bars
from stock_assistant.core.models import Bar, Holding
from stock_assistant.core.utils import log

def moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window

def pct_change(values: list[float], window: int) -> float | None:
    if len(values) <= window or values[-window - 1] == 0:
        return None
    return (values[-1] / values[-window - 1] - 1) * 100

def rsi(values: list[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    recent = changes[-window:]
    gains = [max(change, 0) for change in recent]
    losses = [abs(min(change, 0)) for change in recent]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)

def max_drawdown_from_high(values: list[float], window: int = 120) -> float | None:
    recent = values[-window:]
    if not recent:
        return None
    high = max(recent)
    if high == 0:
        return None
    return (recent[-1] / high - 1) * 100

def volatility(values: list[float], window: int = 20) -> float | None:
    if len(values) <= window:
        return None
    returns = [(values[index] / values[index - 1] - 1) * 100 for index in range(len(values) - window, len(values))]
    return statistics.pstdev(returns) if len(returns) >= 2 else None

def decide_action(
    close: float,
    ma20: float | None,
    ma60: float | None,
    ma120: float | None,
    rsi14: float | None,
    drawdown: float | None,
    profit_pct: float | None,
    weight: float | None,
    config: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    risk_reasons: list[str] = []
    buy_reasons: list[str] = []

    if profit_pct is not None and profit_pct <= float(config["analysis"]["loss_alert_pct"]):
        risk_reasons.append(f"持仓收益 {profit_pct:.2f}% 已触发亏损警戒")
    if weight is not None and weight >= float(config["analysis"]["max_single_position_pct"]):
        risk_reasons.append(f"单只仓位 {weight:.2f}% 偏高")
    if ma60 is not None and close < ma60:
        risk_reasons.append("收盘价低于 MA60，中期趋势偏弱")
    if ma20 is not None and ma60 is not None and ma20 < ma60:
        risk_reasons.append("MA20 低于 MA60，短中期均线未修复")
    if drawdown is not None and drawdown <= -12:
        risk_reasons.append(f"距 120 日高点回撤 {drawdown:.2f}%")
    if rsi14 is not None and rsi14 >= 75:
        risk_reasons.append(f"RSI14={rsi14:.2f}，短线过热")

    if ma20 is not None and ma60 is not None and close > ma20 > ma60:
        buy_reasons.append("价格站上 MA20 且 MA20 高于 MA60")
    if ma120 is not None and close > ma120:
        buy_reasons.append("价格位于 MA120 上方")
    if rsi14 is not None and 45 <= rsi14 <= 68:
        buy_reasons.append(f"RSI14={rsi14:.2f}，未明显过热")

    if len(risk_reasons) >= 2:
        return "减仓/暂停加仓", risk_reasons
    if risk_reasons:
        return "持有观察", risk_reasons + buy_reasons[:1]
    if len(buy_reasons) >= 2:
        reasons.extend(buy_reasons)
        return "可分批加仓", reasons
    return "持有观察", buy_reasons or ["趋势信号不充分"]

def analyze_one(holding: Holding, bars: list[Bar], config: dict[str, Any], total_value: float | None) -> dict[str, Any]:
    min_days = int(config["analysis"]["min_history_days"])
    if len(bars) < min_days:
        return {
            "holding": holding,
            "ok": False,
            "action": "数据不足",
            "reason": f"K 线只有 {len(bars)} 条，低于阈值 {min_days}",
        }

    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    latest = bars[-1]
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    ret5 = pct_change(closes, 5)
    ret20 = pct_change(closes, 20)
    rsi14 = rsi(closes)
    drawdown = max_drawdown_from_high(closes)
    vol20 = volatility(closes)
    vol_ratio = None
    if len(volumes) >= 21 and moving_average(volumes[:-1], 20):
        vol_ratio = volumes[-1] / moving_average(volumes[:-1], 20)

    profit_pct = holding.profit_pct
    if profit_pct is None and holding.cost_price and holding.cost_price > 0:
        profit_pct = (latest.close / holding.cost_price - 1) * 100

    current_value = holding.market_value
    if current_value is None and holding.quantity:
        current_value = holding.quantity * latest.close
    weight = current_value / total_value * 100 if current_value and total_value else None

    action, reasons = decide_action(
        latest.close,
        ma20,
        ma60,
        ma120,
        rsi14,
        drawdown,
        profit_pct,
        weight,
        config,
    )
    return {
        "holding": holding,
        "ok": True,
        "latest": latest,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ret5": ret5,
        "ret20": ret20,
        "rsi14": rsi14,
        "drawdown": drawdown,
        "vol20": vol20,
        "vol_ratio": vol_ratio,
        "profit_pct": profit_pct,
        "current_value": current_value,
        "weight": weight,
        "action": action,
        "reason": "；".join(reasons),
    }

def analyze_holdings(holdings: list[Holding], config: dict[str, Any]) -> list[dict[str, Any]]:
    total_value = sum(item.market_value or 0 for item in holdings) or None
    results: list[dict[str, Any]] = []
    for holding in holdings:
        if holding.asset_type == "fund":
            results.append({
                "holding": holding, 
                "ok": True, 
                "action": "持有场外基金", 
                "reason": "场外基金，不参与K线分析",
                "profit_pct": holding.profit_pct,
                "current_value": holding.market_value,
                "weight": holding.market_value / total_value * 100 if holding.market_value and total_value else None
            })
            continue
        try:
            log(f"拉取行情并分析: {holding.code} {holding.name}")
            bars = fetch_bars(holding.code, config)
            results.append(analyze_one(holding, bars, config, total_value))
        except Exception as exc:  # noqa: BLE001
            results.append({"holding": holding, "ok": False, "action": "行情失败", "reason": str(exc)})
    return results
