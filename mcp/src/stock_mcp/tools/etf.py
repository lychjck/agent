from typing import List
from pydantic import BaseModel, Field

from stock_mcp.registry import registry
from stock_mcp.context import ToolContext
from stock_mcp.providers import EastmoneyFundClient, extract_code

class EtfConstituentsArgs(BaseModel):
    codes: List[str] = Field(..., description="ETF代码列表")

@registry.register("get_etf_constituents", "获取ETF基金的十大重仓持股明细", EtfConstituentsArgs)
def get_etf_constituents(args: EtfConstituentsArgs, ctx: ToolContext) -> dict:
    fund_client = EastmoneyFundClient(ctx.config)
    results = {}
    
    for code in args.codes:
        clean = extract_code(code)
        if not clean:
            continue
        try:
            holdings = fund_client.fetch_fund_holdings(clean)
            results[code] = {"ok": True, "constituents": holdings, "count": len(holdings), "source": "eastmoney_fund"}
        except Exception as e:
            results[code] = {"ok": False, "error": str(e)}
            
    return {"ok": True, "results": results}
