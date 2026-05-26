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

class FundPerformanceArgs(BaseModel):
    code: str = Field(..., description="基金代码，例如 '513050' 或 '007466'")

class PortfolioXirrArgs(BaseModel):
    code: str = Field(..., description="股票或基金代码，例如 '007466'（场外）或 '512880'（场内ETF）")

@registry.register("get_fund_performance_metrics", "获取指定基金的历史绩效指标(最大回撤、年化波动率、夏普比率、卡玛比率)", FundPerformanceArgs)
def get_fund_performance_metrics(args: FundPerformanceArgs, ctx: ToolContext) -> dict:
    from stock_mcp.providers.eastmoney_fund import EastmoneyFundClient
    from stock_mcp.core.math import calculate_performance_metrics
    
    fund_client = EastmoneyFundClient(ctx.config)
    try:
        info = fund_client.fetch_fund_info(args.code)
        if not info or "Data_netWorthTrend" not in info:
            return {"ok": False, "error_type": "no_data", "message": f"未能在天天基金获取到 {args.code} 的历史净值序列数据"}
        
        net_worth = info["Data_netWorthTrend"]
        metrics = calculate_performance_metrics(net_worth)
        
        # 补充一些基本信息
        base_info = fund_client.fetch_fund_base_info(args.code) or {}
        
        return {
            "ok": True,
            "code": args.code,
            "name": base_info.get("name") or info.get("fct", args.code),
            "official_type": base_info.get("official_type") or "Unknown",
            "metrics": metrics
        }
    except Exception as e:
        return {"ok": False, "error_type": "internal_error", "message": str(e)}

@registry.register("calculate_portfolio_xirr", "计算指定基金或股票在账户中的真实年化内部收益率 XIRR (场外真实流水 + 场内持有期等效模型)", PortfolioXirrArgs)
def calculate_portfolio_xirr(args: PortfolioXirrArgs, ctx: ToolContext) -> dict:
    from stock_mcp.providers.tzzb import TzzbClient, extract_code
    from stock_mcp.core.math import calculate_xirr, parse_date
    import datetime
    
    tzzb_client = TzzbClient(ctx.config)
    clean_code = extract_code(args.code)
    
    try:
        # 1. 尝试获取用户所有的记账本持仓
        holdings, _ = tzzb_client.fetch_holdings()
        
        target_holding = None
        for h in holdings:
            if extract_code(h.code) == clean_code:
                target_holding = h
                break
                
        if not target_holding:
            return {
                "ok": False,
                "error_type": "holding_not_found",
                "message": f"在您的记账本持仓中未找到标的 {args.code}，计算 XIRR 必须有当前持仓数据"
            }
            
        # 2. 判断场内 vs 场外
        is_outer_fund = False
        cash_flows = []
        model_type = "real_transactions"
        
        # 尝试拉取场外基金交易流水
        try:
            trans_list = tzzb_client.fetch_fund_trans_history(clean_code)
            if trans_list:
                is_outer_fund = True
                
                # 构建申购确权、赎回、分红现金流
                # op_type 映射: 4代表申购确权(资金流出, amt < 0), 2代表分红(资金流入, amt > 0), 3与5代表赎回/转出(资金流入, amt > 0)
                for t in trans_list:
                    d = t.get("trans_date")
                    amt = float(t.get("trans_amt") or 0)
                    op = str(t.get("op_type", ""))
                    
                    if amt <= 0:
                        continue
                        
                    if op in ("4", "100"):  # 申购确权或初始同步
                        cash_flows.append((d, -amt))
                    elif op in ("2", "3", "5"):  # 分红或赎回
                        cash_flows.append((d, amt))
                        
                # 将“当前市值”作为期末现金流（流入）加入进去
                today_str = datetime.date.today().strftime("%Y-%m-%d")
                cash_flows.append((today_str, target_holding.value))
        except Exception:
            pass
            
        # 3. 兜底及场内资产：使用“持有期等效现金流模型”
        if not is_outer_fund or len(cash_flows) < 2:
            model_type = "equivalent_holding_model"
            cash_flows = []
            
            # 使用同花顺物理返回的持仓天数、成本和已实现平仓盈亏
            hold_days = target_holding.hold_days
            close_profit = target_holding.close_profit
            
            if hold_days <= 0:
                # 若持有天数异常，兜底假定为 30 天
                hold_days = 30.0
                
            # 期初现金流 (持有成本，流出)
            initial_cost = target_holding.cost * target_holding.amount
            if initial_cost <= 0:
                # 兜底直接使用当前市值 - 当前浮盈
                initial_cost = target_holding.value - target_holding.profit
                if initial_cost <= 0:
                    initial_cost = target_holding.value
                    
            today = datetime.date.today()
            start_date = today - datetime.timedelta(days=int(hold_days))
            
            cash_flows.append((start_date.strftime("%Y-%m-%d"), -initial_cost))
            
            # 期末现金流 (当前市值 + 已实现平仓高抛利润，流入)
            terminal_value = target_holding.value + close_profit
            cash_flows.append((today.strftime("%Y-%m-%d"), terminal_value))
            
        # 4. 执行 XIRR 核心计算
        xirr_val = calculate_xirr(cash_flows)
        
        return {
            "ok": True,
            "code": clean_code,
            "name": target_holding.name,
            "model_type": model_type,
            "holding_value": target_holding.value,
            "holding_cost": target_holding.cost * target_holding.amount,
            "hold_days": target_holding.hold_days if target_holding.hold_days > 0 else (target_holding.value - target_holding.profit), # 物理返回的持有天数
            "close_profit": target_holding.close_profit,  # 平仓已实现盈亏 (网格已落袋收益)
            "xirr": xirr_val,  # 真实的年化收益率
            "cash_flows": [{"date": str(d), "amount": amt} for d, amt in cash_flows]
        }
    except Exception as e:
        return {"ok": False, "error_type": "internal_error", "message": str(e)}

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
