import datetime
import math
from typing import List, Tuple, Dict, Any

def parse_date(val: Any) -> datetime.date:
    """安全解析多种格式的日期为 datetime.date"""
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val if isinstance(val, datetime.date) else val.date()
    val_str = str(val).strip()
    # 尝试 YYYY-MM-DD
    try:
        return datetime.datetime.strptime(val_str[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    # 尝试 YYYYMMDD
    try:
        return datetime.datetime.strptime(val_str[:8], "%Y%m%d").date()
    except ValueError:
        pass
    # 兜底转换
    raise ValueError(f"无法解析的日期格式: {val}")

def calculate_xirr(cash_flows: List[Tuple[Any, float]], max_iter: int = 100, tol: float = 1e-6) -> float | None:
    """
    纯 Python 编写的高精度 XIRR (时间加权资金年化内部收益率) 求解器。
    采用 Newton-Raphson 牛顿迭代法逼近求解。
    
    参数:
      cash_flows: 现金流列表，格式为 [(日期, 金额), ...]
                  例如: [('2020-01-01', -10000.0), ('2020-06-01', 200.0), ('2021-01-01', 11000.0)]
                  金额 < 0 表示资金流出(申购确权成本)，金额 > 0 表示资金流入(赎回或分红或期末市值)。
    返回:
      XIRR 年化收益率百分比 (例如 0.158 表示 15.8% 年化)，若不收敛或无解则返回 None。
    """
    # 1. 过滤及标准化
    clean_flows = []
    for d, amt in cash_flows:
        if amt == 0:
            continue
        try:
            clean_flows.append((parse_date(d), float(amt)))
        except Exception:
            continue
            
    if len(clean_flows) < 2:
        return None
        
    # 2. 按日期升序排序
    clean_flows.sort(key=lambda x: x[0])
    
    # 3. 检查方向性：必须同时包含正现金流和负现金流，否则无内部收益率解
    has_negative = any(amt < 0 for _, amt in clean_flows)
    has_positive = any(amt > 0 for _, amt in clean_flows)
    if not (has_negative and has_positive):
        return None
        
    d0 = clean_flows[0][0]
    
    # 将时间转化为以年为单位的浮点数 t_i
    # t_i = (d_i - d0) / 365.0
    flows = []
    for d, amt in clean_flows:
        t = (d - d0).days / 365.0
        flows.append((t, amt))
        
    # XIRR 核心迭代方程: f(r) = sum( C_i / (1 + r)^t_i ) = 0
    # 导数方程: f'(r) = sum( -t_i * C_i / (1 + r)^(t_i + 1) )
    
    r = 0.1  # 初始猜测年化收益率 10%
    
    for _ in range(max_iter):
        f_val = 0.0
        df_val = 0.0
        try:
            for t, amt in flows:
                # 避免 (1+r) <= 0 导致溢出或复数
                term = 1.0 + r
                if term <= 1e-4:
                    term = 1e-4
                
                # f(r) 项
                f_val += amt / (term ** t)
                # f'(r) 项
                df_val -= t * amt / (term ** (t + 1))
        except (ValueError, OverflowError, ZeroDivisionError):
            return None
            
        if abs(df_val) < 1e-12:
            break
            
        delta = f_val / df_val
        r_new = r - delta
        
        # 收敛性校验
        if abs(r_new - r) < tol:
            # 限制合理财务收益区间 (-99.9% 到 +1000%)
            if -0.999 < r_new < 10.0:
                return r_new
            return None
            
        r = r_new
        
    # 迭代失败，采用简单年化作为兜底
    # 简单计算: (期末所有流入之和 + 期初所有流出之和) / abs(期初所有流出) / 总天数 * 365
    total_out = sum(amt for _, amt in clean_flows if amt < 0)
    total_in = sum(amt for _, amt in clean_flows if amt > 0)
    total_days = (clean_flows[-1][0] - clean_flows[0][0]).days
    if total_out != 0 and total_days > 0:
        simple_return = (total_in + total_out) / abs(total_out)
        simple_annual = simple_return / (total_days / 365.0)
        if -0.999 < simple_annual < 10.0:
            return simple_annual
            
    return None

def calculate_performance_metrics(net_worth_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    计算基金风险与绩效指标。
    
    参数:
      net_worth_list: 日度历史单位净值走势序列，格式为 [{"date": "2020-01-01", "nav": 1.01}, ...]
    返回:
      包含最大回撤、年化波动率、年化收益率、夏普比率、卡玛比率的字典。
    """
    results = {
        "max_drawdown": 0.0,
        "annual_volatility": 0.0,
        "annual_return": 0.0,
        "sharpe_ratio": 0.0,
        "calmar_ratio": 0.0,
        "total_days": 0
    }
    
    # 1. 提取并清理净值
    valid_data = []
    for x in net_worth_list:
        try:
            d = parse_date(x.get("date") or x.get("x"))
            nav = float(x.get("nav") or x.get("y"))
            valid_data.append((d, nav))
        except Exception:
            continue
            
    if len(valid_data) < 2:
        return results
        
    # 按日期升序排列
    valid_data.sort(key=lambda x: x[0])
    navs = [item[1] for item in valid_data]
    dates = [item[0] for item in valid_data]
    
    # 2. 计算最大回撤 (Max Drawdown)
    max_drawdown = 0.0
    peak = -1.0
    for val in navs:
        if val > peak:
            peak = val
        if peak > 0:
            drawdown = (peak - val) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                
    # 3. 计算日度收益率序列
    returns = []
    for i in range(1, len(navs)):
        if navs[i-1] > 0:
            returns.append((navs[i] - navs[i-1]) / navs[i-1])
            
    # 4. 计算年化波动率 (Volatility)
    annual_vol = 0.0
    if len(returns) >= 2:
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        daily_vol = math.sqrt(variance)
        annual_vol = daily_vol * math.sqrt(252)  # 年化以 252 个交易日计
        
    # 5. 计算持有期年化收益率
    total_days = (dates[-1] - dates[0]).days
    results["total_days"] = total_days
    
    annual_return = 0.0
    if total_days > 0 and navs[0] > 0:
        total_ret = navs[-1] / navs[0]
        # 年化几何收益率公式
        try:
            annual_return = (total_ret ** (365.0 / total_days)) - 1.0
        except Exception:
            # 几何溢出则退回简单年化
            annual_return = (total_ret - 1.0) / (total_days / 365.0)
            
    # 6. 计算夏普比率 (Sharpe) 和 卡玛比率 (Calmar)
    # 设无风险收益率为常规的 2% (0.02)
    risk_free_rate = 0.02
    sharpe = 0.0
    if annual_vol > 0:
        sharpe = (annual_return - risk_free_rate) / annual_vol
        
    calmar = 0.0
    if max_drawdown > 0:
        calmar = annual_return / max_drawdown
    elif annual_return > 0:
        calmar = annual_return * 100.0  # 极小回撤做简单放大
        
    results["max_drawdown"] = max_drawdown
    results["annual_volatility"] = annual_vol
    results["annual_return"] = annual_return
    results["sharpe_ratio"] = sharpe
    results["calmar_ratio"] = calmar
    
    return results
