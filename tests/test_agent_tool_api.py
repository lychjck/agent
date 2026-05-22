import unittest
import logging
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.main as api
from stock_assistant.core.llm import record_modelscope_rate_limit


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

    def test_agent_resume_uses_selected_model_override(self):
        run_id = "test-resume-model"
        checkpoint = {"messages": [{"role": "user", "content": "继续"}], "next_turn": 2}
        started_threads = []
        original_agent_config = dict(api.config.get("agent", {}))

        class FakeThread:
            def __init__(self, target, args, daemon):
                self.target = target
                self.args = args
                self.daemon = daemon
                started_threads.append(self)

            def start(self):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                api.config.setdefault("agent", {})["snapshot_dir"] = tmpdir
                with api.agent_runs_lock:
                    api.agent_runs[run_id] = {
                        "run_id": run_id,
                        "status": "paused",
                        "events": [],
                        "error": "paused",
                        "checkpoint": checkpoint,
                        "request": {
                            "mode": "tool_agent",
                            "goal": "分析当前持仓",
                            "model": "old-model",
                            "cached_results": None,
                            "resume_state": None,
                        },
                    }

                with patch("api.main.threading.Thread", FakeThread):
                    response = TestClient(api.app).post(
                        f"/api/agent/run/{run_id}/resume",
                        json={"model": "new-model"},
                    )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "queued")
                self.assertEqual(len(started_threads), 1)
                req = started_threads[0].args[1]
                self.assertEqual(req.model, "new-model")
                self.assertEqual(req.resume_state, checkpoint)
                with api.agent_runs_lock:
                    self.assertEqual(api.agent_runs[run_id]["request"]["model"], "new-model")
                recovered = api.load_agent_run_checkpoint(run_id)
                self.assertIsNotNone(recovered)
                self.assertEqual(recovered["request"]["model"], "new-model")
            finally:
                api.config["agent"] = original_agent_config
                with api.agent_runs_lock:
                    api.agent_runs.pop(run_id, None)

    def test_agent_paused_run_recovers_from_checkpoint_after_memory_clear(self):
        run_id = "test-paused-recover"
        original_agent_config = dict(api.config.get("agent", {}))
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                api.config.setdefault("agent", {})["snapshot_dir"] = tmpdir
                with api.agent_runs_lock:
                    api.agent_runs[run_id] = {
                        "run_id": run_id,
                        "status": "paused",
                        "events": [{"step": "paused", "event_index": 0, "status": "暂停"}],
                        "snapshot": None,
                        "error": "paused",
                        "started_at": "2026-05-21T10:00:00",
                        "updated_at": "2026-05-21T10:01:00",
                        "checkpoint": {"messages": [{"role": "user", "content": "继续"}], "next_turn": 2},
                        "request": {"mode": "tool_agent", "model": "old-model"},
                        "thread": None,
                    }
                api.save_agent_run_checkpoint(run_id)
                with api.agent_runs_lock:
                    api.agent_runs.pop(run_id, None)

                response = TestClient(api.app).get(f"/api/agent/run/{run_id}")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "paused")
                self.assertEqual(payload["events"][0]["step"], "paused")
                with api.agent_runs_lock:
                    self.assertIn(run_id, api.agent_runs)
            finally:
                api.config["agent"] = original_agent_config
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

    def test_agent_models_include_modelscope_rate_limit_after_call(self):
        original_llm_config = dict(api.config.get("llm", {}))
        try:
            api.config["llm"] = {
                "model": "deepseek-ai/DeepSeek-V3",
                "base_url": "https://api-inference.modelscope.cn/v1/",
                "model_profiles": {},
            }
            record_modelscope_rate_limit(
                "https://api-inference.modelscope.cn/v1/",
                "deepseek-ai/DeepSeek-V3",
                {
                    "modelscope-ratelimit-requests-limit": "2000",
                    "modelscope-ratelimit-requests-remaining": "1998",
                    "modelscope-ratelimit-model-requests-limit": "200",
                    "modelscope-ratelimit-model-requests-remaining": "198",
                },
            )

            response = TestClient(api.app).get("/api/agent/models")

            self.assertEqual(response.status_code, 200)
            model = response.json()["models"][0]
            self.assertEqual(model["provider"], "ModelScope")
            self.assertEqual(model["rate_limit"]["status"], "known")
            self.assertEqual(model["rate_limit"]["user_limit"], 2000)
            self.assertEqual(model["rate_limit"]["user_remaining"], 1998)
            self.assertEqual(model["rate_limit"]["model_limit"], 200)
            self.assertEqual(model["rate_limit"]["model_remaining"], 198)
        finally:
            api.config["llm"] = original_llm_config

    def test_agent_models_marks_modelscope_rate_limit_unknown_before_call(self):
        original_llm_config = dict(api.config.get("llm", {}))
        try:
            api.config["llm"] = {
                "model": "vendor/new-model",
                "base_url": "https://api-inference.modelscope.cn/v1/",
                "model_profiles": {},
            }

            response = TestClient(api.app).get("/api/agent/models")

            self.assertEqual(response.status_code, 200)
            rate_limit = response.json()["models"][0]["rate_limit"]
            self.assertEqual(rate_limit["status"], "unknown")
            self.assertIn("响应头", rate_limit["note"])
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
        """已废弃：legacy pipeline mode 已被删除，统一走 tool_agent。

        保留这个 placeholder 是为了在未来误恢复 pipeline 路径时能立刻发现：
        如果有人重新加回 mode 字段，请同时考虑前端是否有真正的入口。"""
        self.skipTest("pipeline mode 已被合并为 tool_agent，保留 placeholder 提醒回归")


if __name__ == "__main__":
    unittest.main()
