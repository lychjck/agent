import unittest
from pathlib import Path
from unittest.mock import patch

from stock_assistant import (
    DEFAULTS,
    Holding,
    InstrumentClassification,
    generate_portfolio_observations,
    summarize_portfolio,
    value_map_to_pct,
)
from stock_assistant.cli import build_portfolio_profile


class TestPortfolioProfile(unittest.TestCase):
    def setUp(self):
        self.holdings = [
            Holding(code="510300", name="沪深300ETF", market_value=4000, asset_type="etf"),
            Holding(code="512880", name="证券ETF", market_value=3000, asset_type="etf"),
            Holding(code="000259", name="农银区间收益混合", market_value=2000, asset_type="fund"),
            Holding(code="019109", name="未知基金", market_value=1000, asset_type="fund"),
        ]
        self.classifications = {
            "510300": InstrumentClassification(
                code="510300",
                name="沪深300ETF",
                asset_class="broad_index",
                sector="multi_sector",
                theme="csi300",
                region="china_a",
                strategy="passive_index",
                confidence=1.0,
            ),
            "512880": InstrumentClassification(
                code="512880",
                name="证券ETF",
                asset_class="sector_equity",
                sector="financials",
                theme="brokerage",
                region="china_a",
                strategy="passive_index",
                confidence=0.9,
            ),
            "000259": InstrumentClassification(
                code="000259",
                name="农银区间收益混合",
                asset_class="mixed_allocation",
                sector="multi_sector",
                theme="",
                region="china_a",
                strategy="mixed_allocation",
                confidence=0.62,
            ),
        }
        self.config = {
            "classification": {
                "require_user_review_below_confidence": 0.75
            }
        }

    def test_value_map_to_pct(self):
        self.assertEqual(value_map_to_pct({"a": 25, "b": 75}, 100), {"a": 25.0, "b": 75.0})
        self.assertEqual(value_map_to_pct({"a": 25}, 0), {"a": 0.0})

    def test_summarize_portfolio(self):
        summary = summarize_portfolio(self.holdings, self.classifications, self.config)

        self.assertEqual(summary["total_value"], 10000)
        self.assertEqual(summary["position_count"], 4)
        self.assertAlmostEqual(summary["by_asset_class"]["broad_index"], 40.0)
        self.assertAlmostEqual(summary["by_asset_class"]["sector_equity"], 30.0)
        self.assertAlmostEqual(summary["by_asset_class"]["mixed_allocation"], 20.0)
        self.assertAlmostEqual(summary["by_asset_class"]["unknown"], 10.0)
        self.assertAlmostEqual(summary["by_sector"]["financials"], 30.0)
        self.assertAlmostEqual(summary["by_theme"]["brokerage"], 30.0)
        self.assertAlmostEqual(summary["by_strategy"]["passive_index"], 70.0)
        self.assertAlmostEqual(summary["by_region"]["china_a"], 90.0)
        self.assertAlmostEqual(summary["by_asset_type"]["etf"], 70.0)
        self.assertAlmostEqual(summary["by_asset_type"]["fund"], 30.0)
        self.assertAlmostEqual(summary["unknown_classification_pct"], 10.0)
        self.assertAlmostEqual(summary["low_confidence_classification_pct"], 30.0)

        positions = summary["positions"]
        self.assertEqual([item["code"] for item in positions], ["510300", "512880", "000259", "019109"])
        self.assertAlmostEqual(positions[0]["weight"], 40.0)
        self.assertEqual(positions[3]["asset_class"], "unknown")

    def test_generate_portfolio_observations(self):
        summary = summarize_portfolio(self.holdings, self.classifications, self.config)
        observations = generate_portfolio_observations(summary)

        observation_types = {item["type"] for item in observations}
        self.assertIn("largest_position", observation_types)
        self.assertIn("top_asset_class", observation_types)
        self.assertIn("top_sector", observation_types)
        self.assertIn("top_theme", observation_types)
        self.assertIn("unknown_classification", observation_types)
        self.assertIn("low_confidence_classification", observation_types)
        self.assertIn("active_vs_passive", observation_types)
        self.assertIn("on_exchange_vs_off_exchange", observation_types)

        for observation in observations:
            self.assertNotIn("severity", observation)
            self.assertNotIn("limit", observation)
            self.assertNotIn("exceeded", observation)

    @patch("stock_assistant.classify_holding")
    @patch("stock_assistant.load_cached_classification")
    @patch("stock_assistant.classification_from_config")
    @patch("stock_assistant.fetch_tzzb_holdings")
    def test_build_portfolio_profile_from_tzzb(
        self,
        fetch_holdings,
        classification_from_config,
        load_cached_classification,
        classify_holding,
    ):
        fetch_holdings.return_value = (
            self.holdings,
            Path("snapshot.json"),
            {"total_asset": 10000},
        )
        classification_from_config.side_effect = lambda holding, _config: self.classifications.get(
            holding.code,
        )
        load_cached_classification.return_value = None
        config = {
            **DEFAULTS,
            "ledger": {**DEFAULTS["ledger"], "mode": "tzzb_api"},
            "classification": {
                **DEFAULTS["classification"],
                "require_user_review_below_confidence": 0.75,
            },
        }

        profile = build_portfolio_profile(config, holdings_file=None)

        classify_holding.assert_not_called()
        self.assertEqual(profile["source"], "snapshot.json")
        self.assertEqual(profile["ledger_summary"]["total_asset"], 10000)
        self.assertAlmostEqual(profile["summary"]["by_asset_class"]["broad_index"], 40.0)
        self.assertTrue(profile["observations"])
        self.assertEqual(len(profile["classifications"]), 4)

    @patch("stock_assistant.classify_holding")
    @patch("stock_assistant.load_cached_classification")
    @patch("stock_assistant.classification_from_config")
    @patch("stock_assistant.parse_holdings")
    @patch("stock_assistant.archive_holding_file")
    def test_build_portfolio_profile_from_holdings_file(
        self,
        archive_file,
        parse_holdings,
        classification_from_config,
        load_cached_classification,
        classify_holding,
    ):
        archive_file.return_value = Path("archived.csv")
        parse_holdings.return_value = self.holdings
        classification_from_config.side_effect = lambda holding, _config: self.classifications.get(
            holding.code,
        )
        load_cached_classification.return_value = None

        config = {
            **DEFAULTS,
            "classification": {**DEFAULTS["classification"]},
        }

        profile = build_portfolio_profile(config, holdings_file=Path("holdings.csv"))

        classify_holding.assert_not_called()
        self.assertEqual(profile["source"], "archived.csv")
        self.assertEqual(profile["summary"]["position_count"], 4)
        archive_file.assert_called_once_with(Path("holdings.csv"), config)

    @patch("stock_assistant.classify_holding")
    @patch("stock_assistant.fetch_tzzb_holdings")
    def test_build_portfolio_profile_can_refresh_classification(self, fetch_holdings, classify_holding):
        fetch_holdings.return_value = (self.holdings, Path("snapshot.json"), {})
        classify_holding.side_effect = lambda holding, _config: self.classifications.get(
            holding.code,
            InstrumentClassification(code=holding.code, name=holding.name),
        )
        config = {
            **DEFAULTS,
            "ledger": {**DEFAULTS["ledger"], "mode": "tzzb_api"},
        }

        profile = build_portfolio_profile(config, holdings_file=None, refresh_classification=True)

        self.assertEqual(classify_holding.call_count, 4)
        self.assertAlmostEqual(profile["summary"]["by_asset_class"]["broad_index"], 40.0)


if __name__ == "__main__":
    unittest.main()
