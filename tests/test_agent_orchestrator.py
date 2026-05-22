"""api.main 与 agent runtime 的集成测试。

注：旧的 `run_agent_analysis_events` (非工具版 pipeline Agent) 已被删除，
本文件只保留对 `/api/agent/run/stream` 的端到端测试。
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.main as api


class TestAgentOrchestrator(unittest.TestCase):
    def test_agent_stream_endpoint_emits_named_steps(self):
        async def fake_events(*_args, **_kwargs):
            yield {"step": "agent_start", "status": "开始"}
            yield {"step": "done", "status": "完成", "snapshot": {"agent_report": {"summary": {}}}}

        with patch("api.main.run_tool_agent_events", fake_events):
            response = TestClient(api.app).post("/api/agent/run/stream", json={"model": "m"})

        self.assertEqual(response.status_code, 200)
        self.assertIn('"step": "agent_start"', response.text)
        self.assertIn('"step": "done"', response.text)


if __name__ == "__main__":
    unittest.main()
