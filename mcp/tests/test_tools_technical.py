import pytest
from stock_mcp.analytics import calculate_ma, calculate_rsi, calculate_max_drawdown, calculate_stats

def test_analytics_math():
    closes = [float(i) for i in range(1, 26)] # 25 个数
    
    # MA 测试
    assert calculate_ma(closes, 3) == 24.0
    assert calculate_ma(closes, 30) is None
    
    # Drawdown 测试 (长度超过20)
    closes_dd = [100.0] * 20
    closes_dd[1] = 120.0
    closes_dd[2] = 90.0
    closes_dd[3] = 110.0
    closes_dd[4] = 80.0
    closes_dd[5] = 100.0
    assert calculate_max_drawdown(closes_dd) == pytest.approx(33.33, 0.01)
    
    # 短数据 drawdown 应该返回 None
    assert calculate_max_drawdown([100.0, 90.0]) is None
    
    # Stats 测试 (长度不少于20)
    mu, sigma, z = calculate_stats([10.0] * 20)
    assert mu == 10.0
    assert sigma == 0.0
    assert z == 0.0
    
    # 短数据 stats 应该返回 None
    s_mu, s_sigma, s_z = calculate_stats([10.0, 10.0])
    assert s_mu is None
    assert s_sigma is None
    assert s_z is None
    
    # RSI 测试
    closes_rsi = [44.33, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.10, 46.20, 46.30, 46.40]
    rsi = calculate_rsi(closes_rsi, 14)
    assert rsi is not None
    assert 0.0 <= rsi <= 100.0

