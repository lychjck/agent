import datetime as dt
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

from stock_assistant.agents.agent_tools import build_agent_tool_registry
from stock_assistant.agents.agent_executor import truncate_payload
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
