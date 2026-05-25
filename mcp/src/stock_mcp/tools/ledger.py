from typing import Any, List, Optional
from pydantic import BaseModel, Field

from stock_mcp.registry import registry
from stock_mcp.context import ToolContext
from stock_mcp.providers import TzzbClient

class CurrentHoldingsArgs(BaseModel):
    fields: Optional[List[str]] = Field(None, description="过滤返回字段的白名单")

class ClassificationArgs(BaseModel):
    codes: List[str] = Field(..., description="标的代码列表")

@registry.register("get_asset_trend", "获取投资账本总资产与盈亏趋势", CurrentHoldingsArgs)
def get_asset_trend(args: CurrentHoldingsArgs, ctx: ToolContext) -> dict:
    client = TzzbClient(ctx.config)
    try:
        data = client.fetch_asset_trend()
        return {"ok": True, "asset_trend": data}
    except Exception as e:
        return {"ok": False, "error_type": "fetch_error", "message": str(e)}

@registry.register("get_bs_point", "获取指定个股在投资账本上的历史BS买卖点", ClassificationArgs)
def get_bs_point(args: ClassificationArgs, ctx: ToolContext) -> dict:
    client = TzzbClient(ctx.config)
    results = {}
    for code in args.codes:
        try:
            data = client.fetch_bs_point(code)
            results[code] = {"ok": True, "bs_points": data}
        except Exception as e:
            results[code] = {"ok": False, "error": str(e)}
    return {"ok": True, "results": results}

# --- Placeholders ---
def make_placeholder_handler(tool_name: str) -> Any:
    def handler(args: BaseModel, ctx: ToolContext) -> dict:
        return {
            "ok": False,
            "error_type": "capability_unavailable",
            "message": f"工具 '{tool_name}' 需要动态签名 s（来自同花顺混淆算法），当前未实现，请勿重试。"
        }
    return handler

class DummyArgs(BaseModel):
    pass

# We register the placeholders statically. Handler will dynamically exclude them in tools/list.
registry.register("get_trade_history", "获取投资历史账本流水记录（占位）", DummyArgs)(make_placeholder_handler("get_trade_history"))
registry.register("get_daily_pnl", "获取每日盈亏情况（占位）", DummyArgs)(make_placeholder_handler("get_daily_pnl"))
registry.register("get_monthly_pnl", "获取月度盈亏统计（占位）", DummyArgs)(make_placeholder_handler("get_monthly_pnl"))
registry.register("get_yearly_pnl", "获取年度盈亏统计（占位）", DummyArgs)(make_placeholder_handler("get_yearly_pnl"))
