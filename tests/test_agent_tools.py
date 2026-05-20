import datetime as dt
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

from stock_assistant.agents.agent_tools import build_agent_tool_registry
from stock_assistant.agents.agent_loop_state import AgentLoopState
from stock_assistant.agents.agent_executor import ToolObservation, tool_observation_message, truncate_payload
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.config import DEFAULTS
from stock_assistant.core.llm_tools import LlmToolCall
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

    def test_get_portfolio_profile_returns_compact_structured_payload(self):
        workspace = AgentWorkspace(self.config, holdings=self.holdings)
        tool = build_agent_tool_registry(self.config)["get_portfolio_profile"]
        result = tool.handler(tool.args_model(include=["asset_class", "sector", "concentration"]), workspace)

        self.assertIn("portfolio", result)
        self.assertIn("observations", result)
        self.assertIn("by_asset_class", result["portfolio"])
        self.assertIn("top_positions", result["portfolio"])
        self.assertNotIn("positions", result["portfolio"])

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

    def test_technical_observation_message_compacts_for_llm(self):
        call = LlmToolCall(id="call_001", name="get_holding_technical", arguments={"codes": ["510300"]})
        observation = ToolObservation(
            call_id="call_001",
            tool_name="get_holding_technical",
            ok=True,
            result={
                "technical": {
                    "510300": {
                        "ok": True,
                        "latest_date": "2026-05-20",
                        "latest_close": 5.123,
                        "ma20": 5.0,
                        "ma60": 4.8,
                        "ma120": 4.6,
                        "ret5_pct": 1.2,
                        "rsi14": 55.0,
                        "drawdown_from_120d_high_pct": -3.4,
                        "profit_pct": 2.3,
                        "portfolio_weight_pct": 10.5,
                        "technical_observations": "收盘价高于 MA60",
                        "volume_ratio": None,
                    }
                },
                "summary": "返回 1 个标的技术指标",
            },
            summary="返回 1 个标的技术指标",
        )

        message = tool_observation_message(call, observation)

        self.assertIn('"llm_compacted": true', message["content"])
        self.assertIn('"latest_close": 5.123', message["content"])
        self.assertIn("收盘价高于 MA60", message["content"])
        self.assertNotIn('"ma20"', message["content"])
        self.assertNotIn('"volume_ratio"', message["content"])

    def test_technical_observation_message_can_keep_full_payload(self):
        call = LlmToolCall(id="call_001", name="get_holding_technical", arguments={"codes": ["510300"]})
        observation = ToolObservation(
            call_id="call_001",
            tool_name="get_holding_technical",
            ok=True,
            result={
                "technical": {
                    "510300": {
                        "latest_close": 5.123,
                        "ma20": 5.0,
                        "ma60": 4.8,
                    }
                },
                "summary": "返回 1 个标的技术指标",
            },
            summary="返回 1 个标的技术指标",
        )

        message = tool_observation_message(call, observation, compact=False)

        self.assertIn('"ma20": 5.0', message["content"])
        self.assertNotIn('"llm_compacted": true', message["content"])

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

    def test_workspace_registers_web_search_evidence(self):
        workspace = AgentWorkspace(self.config, holdings=self.holdings)
        call = LlmToolCall(id="call_001", name="web_search", arguments={"query": "沪深300ETF 近期表现"})
        results = [
            {
                "engine": "opencli:duckduckgo",
                "title": f"沪深300ETF 行情 {index}",
                "url": f"https://example.com/510300/{index}",
                "snippet": "近期表现摘要",
            }
            for index in range(1, 13)
        ]
        observation = ToolObservation(
            call_id=call.id,
            tool_name=call.name,
            ok=True,
            result={
                "query": "沪深300ETF 近期表现",
                "results": results,
            },
        )

        refs = workspace.record_external_evidence(call, observation)
        context = workspace.build_llm_context()
        snapshot = workspace.build_snapshot({"summary": {}}, model="test-model")

        self.assertEqual(refs[0], "web_search:call_001:1")
        self.assertEqual(refs[-1], "web_search:call_001:12")
        self.assertEqual(len(refs), 12)
        self.assertEqual(observation.result["evidence_refs"], refs)
        self.assertEqual(observation.result["results"][0]["evidence_ref"], "web_search:call_001:1")
        self.assertEqual(observation.result["results"][11]["evidence_ref"], "web_search:call_001:12")
        self.assertIn("web_search:call_001:1", context["evidence_index"])
        self.assertIn("web_search:call_001:12", context["evidence_index"])
        self.assertEqual(context["external_evidence"][0]["facts"]["url"], "https://example.com/510300/1")
        self.assertEqual(snapshot["external_evidence"][-1]["id"], "web_search:call_001:12")

    def test_loop_state_records_structured_web_search_targets(self):
        state = AgentLoopState(messages=[])
        call = LlmToolCall(id="call_001", name="web_search", arguments={})
        observation = ToolObservation(
            call_id=call.id,
            tool_name=call.name,
            ok=True,
            result={
                "queries": ["510300 沪深300ETF 近期表现", "511880 货币ETF 近期表现"],
                "target_codes": ["510300", "511880"],
            },
        )

        state.record_external_coverage(call, observation)

        self.assertEqual(state.web_search_target_codes, ["510300", "511880"])
        self.assertEqual(state.web_search_queries, ["510300 沪深300ETF 近期表现", "511880 货币ETF 近期表现"])

    def test_loop_state_compacts_old_observation_messages(self):
        large_observation = {
            "call_id": "call_001",
            "tool_name": "web_search",
            "ok": True,
            "result": {
                "summary": "返回搜索结果",
                "results": [{"title": f"结果 {index}", "snippet": "x" * 500} for index in range(20)],
                "evidence_refs": ["web_search:call_001:1"],
            },
            "summary": "返回搜索结果",
        }
        state = AgentLoopState(messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "task"},
            {
                "role": "user",
                "content": (
                    "工具调用结果 observation。请基于该结果继续决定下一步：继续调用工具，或输出 final_report。\n"
                    "tool_call_id: call_001\n"
                    "tool_name: web_search\n"
                    f"observation JSON:\n{json.dumps(large_observation, ensure_ascii=False)}"
                ),
            },
            {"role": "assistant", "content": json.dumps({"type": "observation_reflection", "reasoning_summary": "ok"})},
        ])

        compacted = state.compact_messages_for_llm(max_chars=1000, keep_recent=1)

        self.assertGreaterEqual(compacted, 1)
        self.assertIn("[context_compacted]", state.messages[2]["content"])
        self.assertNotIn("x" * 500, state.messages[2]["content"])

    def test_skill_tools_are_read_only_and_return_installed_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = deepcopy(self.config)
            config["skills"]["install_dir"] = tmp
            skill_dir = Path(tmp) / "stock-method"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: stock-method\ndescription: Custom stock method\n---\n\nUse this workflow.\n",
                encoding="utf-8",
            )
            workspace = AgentWorkspace(config, holdings=self.holdings)
            registry = build_agent_tool_registry(config)

            self.assertIn("list_skills", registry)
            self.assertIn("read_skill", registry)
            self.assertTrue(registry["read_skill"].read_only)

            listed = registry["list_skills"].handler(registry["list_skills"].args_model(), workspace)
            content = registry["read_skill"].handler(registry["read_skill"].args_model(name="stock-method"), workspace)

        self.assertEqual(listed["count"], 1)
        self.assertIn("Use this workflow.", content["content"])

    def test_skill_file_tools_read_packaged_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = deepcopy(self.config)
            config["skills"]["install_dir"] = tmp
            skill_dir = Path(tmp) / "stock-method"
            (skill_dir / "references").mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: stock-method\n---\n\nUse refs.\n", encoding="utf-8")
            (skill_dir / "references" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            workspace = AgentWorkspace(config, holdings=self.holdings)
            registry = build_agent_tool_registry(config)

            listed = registry["list_skill_files"].handler(
                registry["list_skill_files"].args_model(name="stock-method"),
                workspace,
            )
            content = registry["read_skill_file"].handler(
                registry["read_skill_file"].args_model(name="stock-method", path="references/guide.md"),
                workspace,
            )

        self.assertIn("references/guide.md", listed["files"])
        self.assertEqual(content["content"], "# Guide\n")

    def test_web_fetch_tool_is_gated_and_returns_text(self):
        workspace = AgentWorkspace(self.config, holdings=self.holdings)
        registry = build_agent_tool_registry(self.config)
        self.assertNotIn("web_fetch", registry)

        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        with patch(
            "stock_assistant.agents.agent_tools.fetch_url_bytes",
            return_value=(b"<html><body><h1>Result</h1></body></html>", "text/html", "https://example.com"),
        ):
            result = registry["web_fetch"].handler(
                registry["web_fetch"].args_model(url="https://example.com"),
                workspace,
            )

        self.assertIn("web_fetch", registry)
        self.assertIn("Result", result["content"])

    def test_web_fetch_accepts_non_ascii_url(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        with patch(
            "stock_assistant.agents.agent_tools.fetch_url_bytes",
            return_value=(b"ok", "text/plain", "https://example.com/?q=%E8%82%A1%E5%B8%82"),
        ) as fetch_url_bytes:
            result = registry["web_fetch"].handler(
                registry["web_fetch"].args_model(url="https://example.com/?q=股市"),
                workspace,
            )

        fetch_url_bytes.assert_called_once_with("https://example.com/?q=股市", timeout_seconds=20)
        self.assertEqual(result["content"], "ok")

    def test_web_search_and_read_tools_are_available_when_enabled(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        config["search"]["providers"]["opencli"] = {"command_path": "/opt/bin/opencli"}
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        self.assertIn("web_search", registry)
        self.assertIn("web_read", registry)
        self.assertIn("opencli_command", registry)

        with patch("stock_assistant.integrations.search.subprocess.run") as run:
            run.return_value = MagicMock(
                returncode=0,
                stdout='[{"title":"行情","url":"https://example.com/a","snippet":"摘要"}]',
                stderr="",
            )
            search = registry["web_search"].handler(
                registry["web_search"].args_model(query="今天股市行情", engines=["legacy_engine"], max_results=3),
                workspace,
            )
            search_cmd = run.call_args.args[0]
        with patch(
            "stock_assistant.agents.agent_tools.fetch_url_bytes",
            return_value=(
                "<html><body><script>noise()</script><p>正文行情</p></body></html>".encode("utf-8"),
                "text/html",
                "https://example.com/a",
            ),
        ):
            read = registry["web_read"].handler(
                registry["web_read"].args_model(url="https://example.com/a"),
                workspace,
            )

        self.assertEqual(search["count"], 1)
        self.assertEqual(search["engines"], ["opencli"])
        self.assertEqual(search["results"][0]["engine"], "opencli:duckduckgo")
        self.assertEqual(search["results"][0]["url"], "https://example.com/a")
        self.assertEqual(search_cmd[:4], ["/opt/bin/opencli", "duckduckgo", "search", "今天股市行情"])
        self.assertNotIn("legacy_engine", search_cmd)
        self.assertIn("正文行情", read["content"])
        self.assertNotIn("noise", read["content"])

    def test_web_search_splits_multi_holding_query(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        config["search"]["providers"]["opencli"] = {"command_path": "/opt/bin/opencli"}
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        def fake_run(cmd, **kwargs):
            query = cmd[3]
            return MagicMock(
                returncode=0,
                stdout=f'[{{"title":"{query}","url":"https://example.com","snippet":"摘要"}}]',
                stderr="",
            )

        with patch("stock_assistant.integrations.search.subprocess.run", side_effect=fake_run) as run:
            search = registry["web_search"].handler(
                registry["web_search"].args_model(query="510300 沪深300ETF, 511880 货币ETF 近期行情与驱动因素"),
                workspace,
            )

        self.assertTrue(search["split"])
        self.assertEqual(search["queries"], [
            "510300 沪深300ETF 近期 表现 驱动因素",
            "511880 货币ETF 近期 表现 驱动因素",
        ])
        self.assertEqual(search["count"], 2)
        self.assertEqual(run.call_count, 2)

    def test_web_search_targets_generate_one_query_per_holding(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        config["search"]["providers"]["opencli"] = {"command_path": "/opt/bin/opencli"}
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        def fake_run(cmd, **kwargs):
            query = cmd[3]
            return MagicMock(
                returncode=0,
                stdout=f'[{{"title":"{query}","url":"https://example.com","snippet":"摘要"}}]',
                stderr="",
            )

        with patch("stock_assistant.integrations.search.subprocess.run", side_effect=fake_run) as run:
            search = registry["web_search"].handler(
                registry["web_search"].args_model(
                    targets=[
                        {"code": "510300", "name": "沪深300ETF", "topic": "近期表现 驱动因素"},
                        {"code": "511880", "name": "货币ETF", "topic": "近期表现 风险点"},
                    ],
                ),
                workspace,
            )

        self.assertTrue(search["split"])
        self.assertEqual(search["target_codes"], ["510300", "511880"])
        self.assertEqual(search["queries"], [
            "510300 沪深300ETF 近期表现 驱动因素",
            "511880 货币ETF 近期表现 风险点",
        ])
        self.assertEqual(run.call_count, 2)

    def test_web_search_targets_normalize_dirty_model_keys_and_codes(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        config["search"]["providers"]["opencli"] = {"command_path": "/opt/bin/opencli"}
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        def fake_run(cmd, **kwargs):
            query = cmd[3]
            return MagicMock(
                returncode=0,
                stdout=f'[{{"title":"{query}","url":"https://example.com","snippet":"摘要"}}]',
                stderr="",
            )

        with patch("stock_assistant.integrations.search.subprocess.run", side_effect=fake_run):
            search = registry["web_search"].handler(
                registry["web_search"].args_model(
                    targets=[
                        {"．code": "511 880", "name": "货币ETF", "topic": "近期表现"},
                    ],
                ),
                workspace,
            )

        self.assertEqual(search["target_codes"], ["511880"])
        self.assertEqual(search["queries"], ["511880 货币ETF 近期表现"])

    def test_web_search_targets_executes_only_first_batch(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        config["search"]["providers"]["opencli"] = {"command_path": "/opt/bin/opencli"}
        holdings = [
            Holding(code=f"51030{index}", name=f"ETF{index}", market_value=1000 - index, asset_type="etf")
            for index in range(6)
        ]
        workspace = AgentWorkspace(config, holdings=holdings)
        registry = build_agent_tool_registry(config)

        def fake_run(cmd, **kwargs):
            query = cmd[3]
            return MagicMock(
                returncode=0,
                stdout=f'[{{"title":"{query}","url":"https://example.com","snippet":"摘要"}}]',
                stderr="",
            )

        with patch("stock_assistant.integrations.search.subprocess.run", side_effect=fake_run) as run:
            search = registry["web_search"].handler(
                registry["web_search"].args_model(
                    targets=[
                        {"code": holding.code, "name": holding.name}
                        for holding in holdings
                    ],
                ),
                workspace,
            )

        self.assertEqual(search["target_batch_limit"], 4)
        self.assertEqual(search["target_codes"], ["510300", "510301", "510302", "510303"])
        self.assertEqual(search["omitted_target_codes"], ["510304", "510305"])
        self.assertEqual(run.call_count, 4)

    def test_web_search_query_prefers_code_matches_over_overlapping_names(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        config["search"]["providers"]["opencli"] = {"command_path": "/opt/bin/opencli"}
        holdings = [
            Holding(code="512890", name="红利低波ETF", market_value=1000, asset_type="etf"),
            Holding(code="007466", name="华泰柏瑞中证红利低波ETF联接A", market_value=500, asset_type="fund"),
        ]
        workspace = AgentWorkspace(config, holdings=holdings)
        registry = build_agent_tool_registry(config)

        def fake_run(cmd, **kwargs):
            query = cmd[3]
            return MagicMock(
                returncode=0,
                stdout=f'[{{"title":"{query}","url":"https://example.com","snippet":"摘要"}}]',
                stderr="",
            )

        with patch("stock_assistant.integrations.search.subprocess.run", side_effect=fake_run) as run:
            search = registry["web_search"].handler(
                registry["web_search"].args_model(
                    query="007466 华泰柏瑞中证红利低波ETF联接A 近期表现与驱动因素"
                ),
                workspace,
            )

        self.assertFalse(search["split"])
        self.assertEqual(search["queries"], ["007466 华泰柏瑞中证红利低波ETF联接A 近期表现与驱动因素"])
        self.assertEqual(run.call_count, 1)

    def test_web_read_marks_low_quality_login_pages(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        with patch(
            "stock_assistant.agents.agent_tools.fetch_url_bytes",
            return_value=("请登录 注册 验证码 扫码 立即登录".encode("utf-8"), "text/html", "https://xueqiu.com/a"),
        ):
            read = registry["web_read"].handler(
                registry["web_read"].args_model(url="https://xueqiu.com/a"),
                workspace,
            )

        self.assertEqual(read["content_quality"], "low")
        self.assertIn("质量低", read["summary"])

    def test_opencli_command_tool_runs_allowed_read_command(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        config["search"]["providers"]["opencli"] = {"command_path": "/opt/bin/opencli"}
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        with patch("stock_assistant.agents.agent_tools.subprocess.run") as run:
            run.return_value = MagicMock(
                returncode=0,
                stdout='[{"code":"510300","name":"沪深300ETF"}]',
                stderr="",
            )
            result = registry["opencli_command"].handler(
                registry["opencli_command"].args_model(
                    site="eastmoney",
                    command="quote",
                    positionals=["510300"],
                    options={},
                ),
                workspace,
            )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["result"][0]["code"], "510300")
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["/opt/bin/opencli", "eastmoney", "quote", "510300", "-f", "json"],
        )

    def test_opencli_quote_falls_back_for_index_symbol(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        config["search"]["providers"]["opencli"] = {"command_path": "/opt/bin/opencli"}
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        with patch("stock_assistant.agents.agent_tools.subprocess.run") as run:
            run.side_effect = [
                MagicMock(returncode=1, stdout="", stderr="not found"),
                MagicMock(returncode=0, stdout='{"symbol":"HSI"}', stderr=""),
            ]
            result = registry["opencli_command"].handler(
                registry["opencli_command"].args_model(
                    site="eastmoney",
                    command="quote",
                    positionals=["HSI"],
                    options={},
                ),
                workspace,
            )

        self.assertEqual(result["site"], "xueqiu")
        self.assertEqual(result["command"], "stock")
        self.assertEqual(run.call_count, 2)

    def test_opencli_command_tool_rejects_unallowed_command(self):
        config = deepcopy(self.config)
        config["agent"]["allow_external_search_tools"] = True
        workspace = AgentWorkspace(config, holdings=self.holdings)
        registry = build_agent_tool_registry(config)

        with self.assertRaises(ValueError):
            registry["opencli_command"].handler(
                registry["opencli_command"].args_model(site="twitter", command="post", positionals=[], options={}),
                workspace,
            )

    def test_truncate_payload_preserves_web_read_fields(self):
        payload = {
            "summary": "读取页面",
            "url": "https://example.com",
            "final_url": "https://example.com",
            "content_type": "text/html",
            "content": "正文" * 1000,
        }

        truncated = truncate_payload(payload, 800)

        self.assertEqual(truncated["summary"], "读取页面")
        self.assertEqual(truncated["url"], "https://example.com")
        self.assertIn("content", truncated)
        self.assertTrue(truncated["truncated"])
        self.assertLessEqual(len(truncated["content"]), len(payload["content"]))


if __name__ == "__main__":
    unittest.main()
