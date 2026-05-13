import datetime as dt
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

from stock_assistant.agents.agent_tools import build_agent_tool_registry
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.config import DEFAULTS
from stock_assistant.core.models import Bar, Holding


def fake_bars() -> list[Bar]:
    return [
        Bar(
            date=dt.date(2026, 1, 1) + dt.timedelta(days=index),
            open=1 + index * 0.01,
            close=1 + index * 0.01,
            high=1 + index * 0.01,
            low=1 + index * 0.01,
            volume=1000,
            amount=10000,
            pct_change=1.0,
        )
        for index in range(140)
    ]


class TestAgentTools(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(DEFAULTS)
        self.holdings = [
            Holding(code="510300", name="沪深300ETF", market_value=1000, profit_pct=5.0, asset_type="etf"),
            Holding(code="511880", name="货币ETF", market_value=500, profit_pct=1.0, asset_type="etf"),
        ]

    def test_get_current_holdings_filters_fields(self):
        workspace = AgentWorkspace(self.config, holdings=self.holdings)
        tool = build_agent_tool_registry(self.config)["get_current_holdings"]
        result = tool.handler(tool.args_model(fields=["code", "name", "source_row", "weight", "pnl"]), workspace)

        self.assertEqual(result["count"], 2)
        self.assertEqual(set(result["holdings"][0]), {"code", "name", "weight_pct", "profit_pct"})
        self.assertNotIn("source_row", result["holdings"][0])

    def test_get_holding_technical_rejects_unknown_code(self):
        workspace = AgentWorkspace(self.config, holdings=self.holdings)
        tool = build_agent_tool_registry(self.config)["get_holding_technical"]

        with self.assertRaises(ValueError):
            tool.handler(tool.args_model(codes=["999999"]), workspace)

    def test_workspace_caches_technical_results(self):
        workspace = AgentWorkspace(self.config, holdings=self.holdings)

        with patch("stock_assistant.agents.agent_workspace.fetch_bars", return_value=fake_bars()) as fetch_bars:
            first = workspace.ensure_technical(["510300"])
            second = workspace.ensure_technical(["510300"])

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        fetch_bars.assert_called_once()

    def test_trace_context_does_not_include_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = deepcopy(self.config)
            config["paths"]["report_dir"] = tmp
            config["agent"]["snapshot_dir"] = tmp
            config["ledger"]["cookie"] = "COOKIE_SHOULD_NOT_LEAK"
            config["llm"]["api_key"] = "KEY_SHOULD_NOT_LEAK"
            workspace = AgentWorkspace(config, holdings=self.holdings)
            context = workspace.build_llm_context()

        serialized = str(context)
        self.assertNotIn("COOKIE_SHOULD_NOT_LEAK", serialized)
        self.assertNotIn("KEY_SHOULD_NOT_LEAK", serialized)


if __name__ == "__main__":
    unittest.main()
