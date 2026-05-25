from typing import List
from pydantic import BaseModel, Field

from stock_mcp.registry import registry
from stock_mcp.context import ToolContext
from stock_mcp.providers import KlineClient, extract_code
from stock_mcp.analytics.technical import analyze_kline
from stock_mcp.core import logger

class HoldingTechnicalArgs(BaseModel):
    codes: List[str] = Field(..., description="证券/基金代码列表")
    lookback_days: int = Field(120, description="回算计算天数")

@registry.register("get_holding_technical", "获取证券/基金的MA、RSI与统计学 z-score 指标", HoldingTechnicalArgs)
def get_holding_technical(args: HoldingTechnicalArgs, ctx: ToolContext) -> dict:
    kline_client = KlineClient(ctx.config)
    results = {}
    
    for code in args.codes:
        clean = extract_code(code)
        if not clean:
            continue
            
        bars = ctx.get_cached_kline(clean)
        if not bars:
            try:
                logger.info(f"Fetching KLines for code: {clean}")
                bars = kline_client.fetch_bars(clean)
                ctx.set_cached_kline(clean, bars)
            except Exception as e:
                logger.warn(f"Failed to fetch KLines for {clean}: {e}")
                results[code] = {"ok": False, "error": str(e)}
                continue
                
        results[code] = {"ok": True, "indicators": analyze_kline(bars, args.lookback_days)}
        
    return {"ok": True, "results": results}
