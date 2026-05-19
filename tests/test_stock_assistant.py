import datetime as dt
import math
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import stock_assistant as sa


class StockAssistantTest(unittest.TestCase):
    def test_parse_holdings_csv_with_chinese_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "holdings.csv"
            path.write_text(
                "证券代码,证券名称,持仓数量,成本价,持仓市值,收益率\n"
                "510300,沪深300ETF,100,4.00,420,5.0%\n",
                encoding="utf-8",
            )

            holdings = sa.parse_holdings(path, sa.DEFAULTS)

        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0].code, "510300")
        self.assertEqual(holdings[0].name, "沪深300ETF")
        self.assertEqual(holdings[0].quantity, 100)
        self.assertEqual(holdings[0].cost_price, 4.0)
        self.assertEqual(holdings[0].market_value, 420)
        self.assertEqual(holdings[0].profit_pct, 5.0)

    def test_analyze_one_returns_observations_without_trade_action(self):
        closes = [1 + index * 0.004 + 0.08 * math.sin(index / 5 + 3) for index in range(140)]
        bars = [
            sa.Bar(
                date=dt.date(2025, 1, 1) + dt.timedelta(days=index),
                open=close,
                close=close,
                high=close * 1.01,
                low=close * 0.99,
                volume=100000 + index,
                amount=1000000 + index,
                pct_change=1.0,
            )
            for index, close in enumerate(closes)
        ]
        holding = sa.Holding(code="510300", name="沪深300ETF", quantity=100, cost_price=1.5, market_value=239)

        result = sa.analyze_one(holding, bars, sa.DEFAULTS, total_value=1000)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "")
        self.assertIn("高于 MA20", result["reason"])

    def test_decide_action_reports_weak_trend_observations(self):
        action, reasons = sa.decide_action(
            close=0.9,
            ma20=0.95,
            ma60=1.0,
            ma120=1.1,
            rsi14=35,
            drawdown=-15,
            profit_pct=-10,
            weight=20,
            config=sa.DEFAULTS,
        )

        self.assertEqual(action, "")
        self.assertGreaterEqual(len(reasons), 2)
        self.assertTrue(any("低于 MA60" in reason for reason in reasons))

    def test_extract_cookie_from_curl(self):
        curl_text = "curl 'https://tzzb.10jqka.com.cn/pc/' -H 'Cookie: userid=dummy; session=dummy'"

        self.assertEqual(sa.extract_cookie_from_curl(curl_text), "userid=dummy; session=dummy")

    def test_tzzb_stock_holding_maps_decimal_rate_to_percent(self):
        holding = sa.tzzb_stock_holding(
            {
                "code": "512880",
                "name": "证券ETF",
                "count": 1000,
                "cost": 1.0,
                "value": 1200,
                "hold_rate": "0.2",
            },
            {"manualname": "测试账户"},
        )

        self.assertEqual(holding.code, "512880")
        self.assertEqual(holding.name, "证券ETF")
        self.assertEqual(holding.quantity, 1000)
        self.assertEqual(holding.cost_price, 1.0)
        self.assertEqual(holding.market_value, 1200)
        self.assertEqual(holding.profit_pct, 20.0)


    @patch('stock_assistant.write_report')
    @patch('stock_assistant.report_markdown')
    @patch('stock_assistant.analyze_holdings')
    @patch('stock_assistant.fetch_tzzb_holdings')
    def test_run_accepts_tzzb_summary_return(self, m_fetch, m_analyze, m_gen, m_write):
        holding = sa.Holding(code="510300", name="300ETF")
        m_fetch.return_value = ([holding], Path("snapshot.json"), {"total_asset": 1000})
        m_analyze.return_value = []
        m_gen.return_value = "report"
        m_write.return_value = Path("report.md")
        config = sa.DEFAULTS.copy()
        config["ledger"] = {"mode": "tzzb_api"}
        
        sa.run(config, holdings_file=None)
        m_fetch.assert_called_once()

if __name__ == "__main__":
    unittest.main()
