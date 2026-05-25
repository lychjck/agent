from typing import Any, Dict, List
from stock_mcp.domain.holding import Holding

def summarize_portfolio(holdings: List[Holding]) -> Dict[str, Any]:
    """生成组合资产及行业画像与警报"""
    total_val = sum(h.value for h in holdings)
    if total_val == 0:
        return {
            "total_value": 0.0,
            "by_asset_class": {},
            "by_sector": {},
            "observations": ["持仓总市值为 0"]
        }
        
    by_asset_class = {}
    by_sector = {}
    
    for h in holdings:
        ac = h.asset_class or "个股"
        by_asset_class[ac] = by_asset_class.get(ac, 0.0) + h.value
        
        sec = h.sector or "未知"
        by_sector[sec] = by_sector.get(sec, 0.0) + h.value
        
    # 归一化比例
    by_asset_class = {k: v / total_val for k, v in by_asset_class.items()}
    by_sector = {k: v / total_val for k, v in by_sector.items()}
    
    # 观察与诊断警报
    observations = []
    
    # 检查集中度
    for h in holdings:
        pct = h.value / total_val
        if pct > 0.20:
            observations.append(f"标的集中度警报: {h.name} ({h.code}) 占比达 {pct*100:.1f}%，超过 20% 安全阈值。")
            
    # 检查科技仓位
    tech_pct = 0.0
    tech_keys = {"technology", "semiconductor", "半导体", "电子", "通信", "计算机", "传媒"}
    for k, v in by_sector.items():
        if k in tech_keys:
            tech_pct += v
            
    if tech_pct > 0.40:
        observations.append(f"行业暴露警告: 科技/半导体仓位占比达 {tech_pct*100:.1f}%，处于超配区间 (>40%)。")
        
    return {
        "total_value": total_val,
        "by_asset_class": by_asset_class,
        "by_sector": by_sector,
        "observations": observations,
    }
