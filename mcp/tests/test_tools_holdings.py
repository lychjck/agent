import pytest
from stock_mcp.domain import Holding
from stock_mcp.analytics import summarize_portfolio
from stock_mcp.server import McpServer, ToolContext
from stock_mcp.tools import save_snapshot, SaveSnapshotArgs, load_snapshot_summary, SnapshotSummaryArgs

def test_portfolio_summary():
    holdings = [
        Holding(code="510300", name="300ETF", amount=1000, value=800.0, asset_class="个股", sector="financials"),
        Holding(code="512890", name="ChipETF", amount=2000, value=200.0, asset_class="个股", sector="semiconductor"),
    ]
    summary = summarize_portfolio(holdings)
    assert summary["total_value"] == 1000.0
    assert summary["by_asset_class"]["个股"] == 1.0
    assert summary["by_sector"]["financials"] == 0.8
    assert summary["by_sector"]["semiconductor"] == 0.2
    assert any("标的集中度警报" in o for o in summary["observations"])

def test_snapshot_lifecycle(tmp_path, base_config):
    config = dict(base_config)
    config["paths"]["snapshots_dir"] = str(tmp_path)
    
    server = McpServer(config)
    ctx = ToolContext(config, "test-req")
    
    snap_data = {
        "holdings": [
            {"code": "510300", "name": "300ETF", "amount": 1000, "value": 800.0}
        ],
        "portfolio_profile": {
            "total_value": 800.0,
            "observations": ["高位预警"]
        }
    }
    
    save_args = SaveSnapshotArgs(snapshot_data=snap_data)
    res_save = save_snapshot(save_args, ctx)
    assert res_save["ok"] is True
    
    load_args = SnapshotSummaryArgs(which="latest")
    res_load = load_snapshot_summary(load_args, ctx)
    assert res_load["ok"] is True
    assert res_load["total_value"] == 800.0
    assert res_load["portfolio_top"] == ["300ETF"]
    assert res_load["risk_count"] == 1

def test_high_precision_classification(base_config):
    from unittest.mock import patch
    from stock_mcp.tools.holdings import get_classifications_for_holdings
    
    # 准备测试持仓标的
    holdings = [
        Holding(code="001697", name="货币基金", amount=1000),
        Holding(code="970165", name="债券基金", amount=2000),
        Holding(code="512890", name="科技精选ETF", amount=3000),
        Holding(code="510300", name="300ETF", amount=4000),
        Holding(code="513100", name="纳指ETF", amount=5000),
        Holding(code="600519", name="贵州茅台", amount=100) # 股票
    ]
    
    # 模拟 EastmoneyFundClient 的接口响应
    with patch("stock_mcp.tools.holdings.EastmoneyFundClient") as MockClient, \
         patch("stock_mcp.tools.holdings.fetch_stock_industry") as mock_fetch_industry:
        
        client_instance = MockClient.return_value
        
        # 定义 base_info 模拟返回值
        def side_effect_base_info(code):
            mapping = {
                "001697": {"name": "广发货币A", "official_type": "货币型-普通货币"},
                "970165": {"name": "招商中短债C", "official_type": "债券型-中短债"},
                "512890": {"name": "科技精选ETF", "official_type": "指数型-股票"},
                "510300": {"name": "沪深300ETF", "official_type": "指数型-股票"},
                "513100": {"name": "纳指ETF国泰", "official_type": "指数型-海外股票"},
            }
            return mapping.get(code)
        client_instance.fetch_fund_base_info.side_effect = side_effect_base_info
        
        # 定义 holdings 模拟返回值（芯片ETF 有 3 只重仓股，300ETF 有 5 只）
        def side_effect_holdings(code):
            if code == "512890":
                return [
                    {"code": "600584", "name": "长电科技"},
                    {"code": "603501", "name": "韦尔股份"},
                    {"code": "600703", "name": "三安光电"}
                ]
            elif code == "510300":
                return [
                    {"code": "600519", "name": "贵州茅台"},
                    {"code": "600036", "name": "招商银行"},
                    {"code": "601318", "name": "中国平安"},
                    {"code": "300750", "name": "宁德时代"},
                    {"code": "600900", "name": "长江电力"}
                ]
            return []
        client_instance.fetch_fund_holdings.side_effect = side_effect_holdings
        
        # 定义个股行业返回模拟
        def side_effect_industry(code, timeout=10.0):
            # 股票直查分支
            if code == "600519":
                return "食品饮料"
            # 芯片ETF 的重仓股全部属于“半导体”
            if code in ("600584", "603501", "600703"):
                return "半导体"
            # 300ETF 的重仓股行业高度分散
            industry_map = {
                "600519": "食品饮料",
                "600036": "银行",
                "601318": "非银金融",
                "300750": "电力设备",
                "600900": "公用事业"
            }
            return industry_map.get(code, "Unknown")
        mock_fetch_industry.side_effect = side_effect_industry
        
        # 执行分类算法
        res = get_classifications_for_holdings(holdings, base_config)
        
        # 1. 股票分类断言
        assert res["600519"]["primary_class"] == "个股"
        assert res["600519"]["sector"] == "食品饮料"
        
        # 2. 货币基金闪电定性断言
        assert res["001697"]["primary_class"] == "货币基金"
        assert res["001697"]["sector"] == "货币资金"
        
        # 3. 债券基金闪电定性断言
        assert res["970165"]["primary_class"] == "债券基金"
        assert res["970165"]["sector"] == "固定收益"
        
        # 4. 芯片ETF（重仓穿透投票半导体）断言
        assert res["512890"]["primary_class"] == "行业主题"
        assert res["512890"]["sector"] == "半导体"
        assert res["512890"]["confidence"] == 1.0  # 3只股票全中半导体，3/3=1.0
        
        # 5. 300ETF（重仓穿透，行业分散）断言
        assert res["510300"]["primary_class"] == "宽基指数"
        assert res["510300"]["sector"] == "宽基大盘"
        
        # 6. 纳指ETF（穿透无持仓，根据关键字兜底匹配）断言
        assert res["513100"]["primary_class"] == "宽基指数"
        assert res["513100"]["sector"] == "海外宽基"
        assert res["513100"]["source"] == "name_keyword_direct"


def test_holdings_filtering_and_trace(base_config):
    from unittest.mock import patch
    from stock_mcp.tools.holdings import get_current_holdings, CurrentHoldingsArgs
    
    # 模拟持仓数据
    holdings = [
        Holding(code="510300", name="300ETF", amount=1000, value=800.0, profit=80.0, profit_rate=10.0, asset_class="宽基指数", sector="宽基大盘"),
        Holding(code="512890", name="红利低波", amount=2000, value=200.0, profit=-20.0, profit_rate=-10.0, asset_class="宽基指数", sector="宽基大盘"),
        Holding(code="512170", name="医疗ETF", amount=3000, value=300.0, profit=-60.0, profit_rate=-20.0, asset_class="行业主题", sector="医药生物"),
    ]
    
    # 模拟 TzzbClient 的 fetch_holdings 响应与分类解析
    with patch("stock_mcp.tools.holdings.TzzbClient") as MockTzzb, \
         patch("stock_mcp.tools.holdings.get_classifications_for_holdings") as mock_get_class:
        
        tzzb_instance = MockTzzb.return_value
        tzzb_instance.fetch_holdings.return_value = (holdings, {})
        
        # 模拟返回对应的分类溯源详情
        mock_get_class.return_value = {
            "510300": {"primary_class": "宽基指数", "sector": "宽基大盘", "source": "name_keyword_direct", "confidence": 0.95, "name": "300ETF"},
            "512890": {"primary_class": "宽基指数", "sector": "宽基大盘", "source": "name_keyword_direct", "confidence": 0.95, "name": "红利低波"},
            "512170": {"primary_class": "行业主题", "sector": "医药生物", "source": "name_keyword_direct", "confidence": 0.95, "name": "医疗ETF"},
        }
        
        ctx = ToolContext(base_config, "test-req")
        
        # 1. 测试资产大类过滤
        args_ac = CurrentHoldingsArgs(asset_class="行业主题")
        res_ac = get_current_holdings(args_ac, ctx)
        assert res_ac["ok"] is True
        assert res_ac["count"] == 1
        assert res_ac["holdings"][0]["code"] == "512170"
        
        # 2. 测试盈亏区间过滤
        args_profit = CurrentHoldingsArgs(max_profit_rate=-5.0)  # 筛选亏损大于5%的标的
        res_profit = get_current_holdings(args_profit, ctx)
        assert res_profit["ok"] is True
        assert res_profit["count"] == 2
        codes = [h["code"] for h in res_profit["holdings"]]
        assert "512890" in codes
        assert "512170" in codes
        
        # 3. 测试 Trace 链路注入
        args_trace = CurrentHoldingsArgs(include_trace=True, asset_class="行业主题")
        res_trace = get_current_holdings(args_trace, ctx)
        assert res_trace["ok"] is True
        assert res_trace["count"] == 1
        target = res_trace["holdings"][0]
        assert "classification_trace" in target
        assert target["classification_trace"]["source"] == "name_keyword_direct"
        assert target["classification_trace"]["confidence"] == 0.95



