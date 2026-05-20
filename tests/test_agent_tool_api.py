import unittest
import logging
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.main as api


class TestAgentToolApi(unittest.TestCase):
    def test_agent_run_polling_access_log_is_filtered(self):
        filter_ = api.AgentRunPollingAccessFilter()
        polling = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='127.0.0.1:50360 - "GET /api/agent/run/agent-ui-1?after=2 HTTP/1.1" 200 OK',
            args=(),
            exc_info=None,
        )
        other = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='127.0.0.1:50360 - "GET /api/overview HTTP/1.1" 200 OK',
            args=(),
            exc_info=None,
        )

        self.assertFalse(filter_.filter(polling))
        self.assertTrue(filter_.filter(other))

    def test_agent_run_events_limit_comes_from_config(self):
        run_id = "test-run"
        original_agent_config = dict(api.config.get("agent", {}))
        try:
            api.config.setdefault("agent", {})["max_run_events"] = 2
            with api.agent_runs_lock:
                api.agent_runs[run_id] = {
                    "run_id": run_id,
                    "status": "running",
                    "events": [],
                }

            api.append_agent_run_event(run_id, {"step": "one"})
            api.append_agent_run_event(run_id, {"step": "two"})
            api.append_agent_run_event(run_id, {"step": "three"})

            with api.agent_runs_lock:
                events = list(api.agent_runs[run_id]["events"])

            self.assertEqual([event["step"] for event in events], ["two", "three"])
            self.assertEqual([event["event_index"] for event in events], [0, 1])
        finally:
            api.config["agent"] = original_agent_config
            with api.agent_runs_lock:
                api.agent_runs.pop(run_id, None)

    def test_agent_run_cancel_marks_run_cancelled(self):
        run_id = "test-cancel"
        with api.agent_runs_lock:
            api.agent_runs[run_id] = {
                "run_id": run_id,
                "status": "running",
                "events": [],
                "error": "",
            }

        try:
            response = TestClient(api.app).post(f"/api/agent/run/{run_id}/cancel")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "cancelled")
            with api.agent_runs_lock:
                record = api.agent_runs[run_id]
                events = list(record["events"])
            self.assertTrue(record["cancel_requested"])
            self.assertEqual(record["status"], "cancelled")
            self.assertEqual(events[-1]["step"], "cancelled")
            self.assertIn("用户已终止", events[-1]["status"])
        finally:
            with api.agent_runs_lock:
                api.agent_runs.pop(run_id, None)

    def test_agent_models_come_from_config(self):
        original_llm_config = dict(api.config.get("llm", {}))
        try:
            api.config["llm"] = {
                "model": "local-model",
                "base_url": "http://127.0.0.1:1234/v1",
                "model_profiles": {
                    "custom-model": {
                        "model": "vendor/custom-model",
                        "base_url": "https://example.com/v1",
                    }
                },
            }

            response = TestClient(api.app).get("/api/agent/models")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["default_model"], "local-model")
            self.assertEqual([item["id"] for item in payload["models"]], ["local-model", "custom-model"])
            self.assertEqual(payload["models"][0]["provider"], "Local")
            self.assertEqual(payload["models"][1]["name"], "custom-model")
        finally:
            api.config["llm"] = original_llm_config

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
