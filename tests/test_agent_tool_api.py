import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.main as api


class TestAgentToolApi(unittest.TestCase):
    def test_tool_agent_stream_endpoint_uses_tool_agent_events(self):
        async def fake_events(*_args, **_kwargs):
            yield {"step": "agent_start", "status": "开始"}
            yield {"step": "tool_call", "tool": "get_current_holdings", "status": "调用工具：get_current_holdings"}
            yield {"step": "tool_observation", "tool": "get_current_holdings", "status": "返回 1 只持仓"}
            yield {"step": "done", "snapshot": {"agent_report": {"summary": {"brief": "ok"}}}}

        with patch("api.main.run_tool_agent_events", fake_events):
            response = TestClient(api.app).post(
                "/api/agent/run/stream",
                json={"mode": "tool_agent", "goal": "分析当前持仓"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"step": "tool_call"', response.text)
        self.assertIn('"brief": "ok"', response.text)

    def test_pipeline_mode_still_uses_pipeline_events(self):
        async def fake_events(*_args, **_kwargs):
            yield {"step": "sync_holdings", "status": "同步"}
            yield {"step": "done", "snapshot": {"agent_report": {"summary": {"brief": "pipeline"}}}}

        with patch("api.main.run_agent_analysis_events", fake_events):
            response = TestClient(api.app).post(
                "/api/agent/run/stream",
                json={"mode": "pipeline"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"brief": "pipeline"', response.text)


if __name__ == "__main__":
    unittest.main()
