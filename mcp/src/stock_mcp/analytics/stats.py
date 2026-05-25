import math
from typing import List, Tuple

def calculate_stats(closes: List[float]) -> Tuple[float | None, float | None, float | None]:
    """计算 均值(mu), 标准差(sigma) 和 今日的 z-score。长度少于 20 天时安全返回 None"""
    n = len(closes)
    if n < 20:
        return None, None, None
    mu = sum(closes) / n
    variance = sum((x - mu) ** 2 for x in closes) / n
    sigma = math.sqrt(variance)
    
    today_val = closes[-1]
    z_score = (today_val - mu) / sigma if sigma > 0 else 0.0
    return mu, sigma, z_score

