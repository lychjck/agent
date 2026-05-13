import asyncio
import datetime as dt
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.main as api
from stock_assistant.agents.agent import run_agent_analysis_events
from stock_assistant.core.config import DEFAULTS
from stock_assistant.core.models import Bar, Holding


def fake_bars() -> list[Bar]:
    bars = []
    for index in range(140):
        close = 1.0 + index * 0.01
        bars.append(
            Bar(
                date=dt.date(2026, 1, 1) + dt.timedelta(days=index),
                open=close,
                close=close,
                high=close * 1.01,
                low=close * 0.99,
                volume=100000 + index,
                amount=1000000 + index,
                pct_change=1.0,
            )
        )
    return bars


async def collect_events(*args, **kwargs):
    events = []
    async for event in run_agent_analysis_events(*args, **kwargs):
        events.append(event)
    return events


class TestAgentOrchestrator(unittest.TestCase):
    def test_run_agent_analysis_events_builds_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = deepcopy(DEFAULTS)
            config["paths"]["report_dir"] = tmp
            config["agent"]["snapshot_dir"] = tmp
            config["llm"]["enabled"] = False
            holdings = [
                Holding(code="510300", name="沪深300ETF", market_value=1000, asset_type="etf"),
                Holding(code="000259", name="农银区间收益混合", market_value=500, asset_type="fund"),
            ]

            with patch("stock_assistant.agents.agent.fetch_bars", return_value=fake_bars()):
                events = asyncio.run(
                    collect_events(
                        config,
                        holdings=holdings,
                        model_override="test-model",
                        save_snapshot=False,
                        save_report=False,
                    )
                )

        steps = [event["step"] for event in events]
        self.assertIn("market_data", steps)
        self.assertIn("technical_analysis", steps)
        self.assertIn("classify", steps)
        self.assertIn("portfolio_profile", steps)
        self.assertIn("llm_report", steps)
        self.assertEqual(steps[-1], "done")
        snapshot = events[-1]["snapshot"]
        self.assertEqual(snapshot["model"], "test-model")
        self.assertEqual(snapshot["portfolio"]["position_count"], 2)
        self.assertIn("agent_report", snapshot)
        self.assertTrue(snapshot["agent_report"]["action_items"])

    def test_agent_stream_endpoint_emits_named_steps(self):
        async def fake_events(*_args, **_kwargs):
            yield {"step": "sync_holdings", "status": "正在同步投资账本"}
            yield {"step": "done", "status": "完成", "snapshot": {"agent_report": {"summary": {}}}}

        with patch("api.main.run_agent_analysis_events", fake_events):
            response = TestClient(api.app).post("/api/agent/run/stream", json={"model": "m"})

        self.assertEqual(response.status_code, 200)
        self.assertIn('"step": "sync_holdings"', response.text)
        self.assertIn('"step": "done"', response.text)

    def test_legacy_analyze_endpoint_maps_done_to_result(self):
        async def fake_events(*_args, **_kwargs):
            yield {
                "step": "technical_analysis",
                "status": "技术分析完成",
                "technical_results": [{"holding": {"code": "510300", "name": "300ETF"}}],
            }
            yield {
                "step": "done",
                "status": "完成",
                "snapshot": {"agent_report": {"summary": {"brief": "ok"}}},
            }

        with patch("api.main.llm_enabled", return_value=True), patch("api.main.run_agent_analysis_events", fake_events):
            response = TestClient(api.app).post("/api/analyze", json={"model": "m"})

        self.assertEqual(response.status_code, 200)
        self.assertIn('"step": 2', response.text)
        self.assertIn('"technical_results"', response.text)
        self.assertIn('"result"', response.text)
        self.assertIn('"brief": "ok"', response.text)


if __name__ == "__main__":
    unittest.main()
