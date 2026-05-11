import unittest
import datetime as dt

from stock_assistant import Holding, Bar, holding_to_dict, bar_to_dict, analysis_result_to_dict

class TestSerialization(unittest.TestCase):
    def test_holding_to_dict(self):
        h = Holding(
            code="510300",
            name="沪深300ETF",
            quantity=100.0,
            cost_price=3.5,
            market_value=400.0,
            profit_pct=14.28,
            hold_profit=50.0,
            day_profit=10.0,
            asset_type="etf"
        )
        d = holding_to_dict(h)
        self.assertEqual(d["code"], "510300")
        self.assertEqual(d["name"], "沪深300ETF")
        self.assertEqual(d["quantity"], 100.0)
        self.assertEqual(d["cost_price"], 3.5)
        self.assertEqual(d["market_value"], 400.0)
        self.assertEqual(d["profit_pct"], 14.28)
        self.assertEqual(d["hold_profit"], 50.0)
        self.assertEqual(d["day_profit"], 10.0)
        self.assertEqual(d["asset_type"], "etf")

    def test_bar_to_dict(self):
        b = Bar(
            date=dt.date(2023, 1, 1),
            open=1.0,
            close=1.1,
            high=1.2,
            low=0.9,
            volume=1000,
            amount=1050,
            pct_change=10.0
        )
        d = bar_to_dict(b)
        self.assertEqual(d["date"], "2023-01-01")
        self.assertEqual(d["close"], 1.1)
        self.assertEqual(d["pct_change"], 10.0)

        self.assertIsNone(bar_to_dict(None))

    def test_analysis_result_to_dict(self):
        h = Holding(code="510300", name="300ETF", asset_type="etf")
        b = Bar(
            date=dt.date(2023, 1, 1),
            open=1.0, close=1.1, high=1.2, low=0.9,
            volume=1000, amount=1050, pct_change=10.0
        )
        
        # With latest
        res1 = {
            "holding": h,
            "latest": b,
            "ok": True,
            "action": "持有观察",
            "reason": "正常波动"
        }
        d1 = analysis_result_to_dict(res1)
        self.assertIsInstance(d1["holding"], dict)
        self.assertEqual(d1["holding"]["code"], "510300")
        self.assertIsInstance(d1["latest"], dict)
        self.assertEqual(d1["latest"]["date"], "2023-01-01")
        self.assertEqual(d1["action"], "持有观察")

        # Without latest
        res2 = {
            "holding": h,
            "ok": False,
            "action": "行情失败",
            "reason": "timeout"
        }
        d2 = analysis_result_to_dict(res2)
        self.assertIsInstance(d2["holding"], dict)
        self.assertNotIn("latest", d2)
        self.assertEqual(d2["action"], "行情失败")

if __name__ == '__main__':
    unittest.main()
