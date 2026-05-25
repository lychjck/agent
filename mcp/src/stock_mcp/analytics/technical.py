from typing import Any, Dict, List

from stock_mcp.domain.holding import Bar
from stock_mcp.analytics.stats import calculate_stats

def calculate_ma(closes: List[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period

def calculate_rsi(closes: List[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_max_drawdown(closes: List[float]) -> float | None:
    if len(closes) < 20:
        return None
    max_dd = 0.0
    peak = closes[0]
    for p in closes:
        if p > peak:
            peak = p
        dd = (peak - p) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd * 100.0

def analyze_kline(bars: List[Bar], lookback: int = 120) -> Dict[str, Any]:
    """计算单个标的的技术与统计学指标"""
    closes = [b.close for b in bars]
    if not closes:
        return {}
    
    # 若 lookback 小于 20 天，直接将 lookback 设置为 0，迫使 stats 计算判定为短天数并安全返回 None
    actual_lookback = lookback if lookback >= 20 else 0
    closes_lookback = closes[-actual_lookback:] if len(closes) >= actual_lookback and actual_lookback > 0 else []
    
    mu, sigma, z_score = calculate_stats(closes_lookback)
    
    daily_pct = 0.0
    if len(closes) >= 2:
        daily_pct = (closes[-1] / closes[-2] - 1) * 100.0
        
    return {
        "ma20": calculate_ma(closes, 20),
        "ma60": calculate_ma(closes, 60),
        "ma120": calculate_ma(closes, 120),
        "rsi14": calculate_rsi(closes, 14),
        "drawdown_120d": calculate_max_drawdown(closes_lookback),
        "vol_120": sigma,
        "mu_120": mu,
        "sigma_120": sigma,
        "z_score_today": z_score,
        "daily_pct_change": daily_pct,
    }

