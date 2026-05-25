from pydantic import BaseModel, Field

class Holding(BaseModel):
    code: str = Field(..., description="证券/基金代码")
    name: str = Field(..., description="证券/基金名称")
    amount: float = Field(..., description="持有份额")
    price: float = Field(0.0, description="当前价格")
    cost: float = Field(0.0, description="持仓成本价")
    value: float = Field(0.0, description="当前市值")
    profit: float = Field(0.0, description="账面盈亏金额")
    profit_rate: float = Field(0.0, description="盈亏比例")
    asset_class: str = Field("Equity", description="资产类别，如 Equity/FixedIncome/Cash/Alternative")
    sector: str = Field("Unknown", description="行业分类")

class Bar(BaseModel):
    date: str = Field(..., description="日期，格式 YYYY-MM-DD")
    open: float = Field(..., description="开盘价")
    close: float = Field(..., description="收盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    volume: float = Field(..., description="成交量")
