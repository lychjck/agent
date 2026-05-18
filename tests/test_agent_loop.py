import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

from stock_assistant.agents.agent_loop import (
    final_report_missing_holding_analysis,
    merge_final_report_patch,
    missing_technical_codes,
    run_tool_agent_events,
    split_oversized_tool_calls,
)
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.config import DEFAULTS
from stock_assistant.core.llm_tools import LlmToolCall, LlmToolStep
from stock_assistant.core.models import Holding


async def collect_events(*args, **kwargs):
    events = []
    async for event in run_tool_agent_events(*args, **kwargs):
        events.append(event)
    return events


class TestAgentLoop(unittest.TestCase):
    def config(self):
        tmp = tempfile.TemporaryDirectory()
        config = deepcopy(DEFAULTS)
        config["paths"]["report_dir"] = tmp.name
        config["agent"]["snapshot_dir"] = tmp.name
        config["agent"]["trace_dir"] = tmp.name
        config["agent"]["tool_agent_enabled"] = True
        config["agent"]["save_traces"] = True
        config["llm"]["enabled"] = True
        self.addCleanup(tmp.cleanup)
        return config

    def test_tool_agent_runs_tool_then_final_report(self):
        config = self.config()
        holdings = [
            {
                "holding": {
                    "code": "510300",
                    "name": "沪深300ETF",
                    "market_value": 1000,
                    "profit_pct": 5.0,
                    "asset_type": "etf",
                },
                "ok": True,
                "action": "持有观察",
                "reason": "趋势正常",
            }
        ]
        responses = [
            LlmToolStep(
                type="research_plan",
                research_plan={
                    "information_needs": ["当前组合权重", "ETF 底层持仓"],
                    "available_tool_mapping": [{"need": "当前组合权重", "tool": "get_current_holdings"}],
                    "missing_capabilities": ["缺少 ETF 底层持仓工具"],
                    "execution_strategy": "先读取已有工具可验证的信息。",
                },
                thinking_trace={
                    "information_needs": ["当前组合权重", "ETF 底层持仓"],
                    "missing_capabilities": ["缺少 ETF 底层持仓工具"],
                    "next_step": "读取当前持仓",
                },
                missing_capabilities=["缺少 ETF 底层持仓工具"],
                raw_text=json.dumps({
                    "type": "research_plan",
                    "reasoning_summary": "先规划信息需求。",
                    "research_plan": {
                        "information_needs": ["当前组合权重", "ETF 底层持仓"],
                        "missing_capabilities": ["缺少 ETF 底层持仓工具"],
                    },
                }),
                reasoning_summary="先规划信息需求。",
            ),
            LlmToolStep(
                type="tool_calls",
                tool_calls=[
                    LlmToolCall(
                        id="call_001",
                        name="get_current_holdings",
                        arguments={"fields": ["code", "name", "weight_pct"]},
                    )
                ],
                raw_text=json.dumps({
                    "type": "tool_calls",
                    "reasoning_summary": "需要先读取当前持仓。",
                    "tool_calls": [{"id": "call_001", "name": "get_current_holdings", "arguments": {}}],
                }),
                reasoning_summary="需要先读取当前持仓。",
            ),
            LlmToolStep(
                type="observation_reflection",
                observation_reflection={
                    "satisfied_needs": ["当前组合权重"],
                    "unsatisfied_needs": ["ETF 底层持仓"],
                    "observation_impact": "已确认当前持仓。",
                    "next_action": "final_report",
                },
                thinking_trace={
                    "known_facts": ["当前持有 510300"],
                    "satisfied_needs": ["当前组合权重"],
                    "unsatisfied_needs": ["ETF 底层持仓"],
                    "next_step": "生成带限制说明的报告",
                },
                missing_capabilities=["缺少 ETF 底层持仓工具"],
                raw_text=json.dumps({
                    "type": "observation_reflection",
                    "reasoning_summary": "已确认当前持仓，可以生成带限制的报告。",
                    "observation_reflection": {
                        "satisfied_needs": ["当前组合权重"],
                        "unsatisfied_needs": ["ETF 底层持仓"],
                        "next_action": "final_report",
                    },
                }),
                reasoning_summary="已确认当前持仓，可以生成带限制的报告。",
            ),
            LlmToolStep(
                type="final_report",
                final_report={
                    "summary": {"status": "review", "brief": "组合可继续观察。"},
                    "holding_analysis": [
                        {
                            "target_code": "510300",
                            "target_name": "沪深300ETF",
                            "action_type": "hold",
                            "title": "继续观察",
                            "reason": "当前没有破坏趋势的证据。",
                            "evidence_refs": ["holding:510300:technical"],
                        }
                    ],
                },
                raw_text=json.dumps({
                    "type": "final_report",
                    "reasoning_summary": "已有足够 observation。",
                    "report": {"summary": {"brief": "组合可继续观察。"}},
                }),
                reasoning_summary="已有足够 observation。",
            ),
        ]

        with patch("stock_assistant.agents.agent_loop.call_llm_tool_step", side_effect=responses):
            events = asyncio.run(
                collect_events(
                    config,
                    goal="分析当前持仓",
                    cached_results=holdings,
                    save_snapshot=False,
                    save_report=False,
                )
            )

        steps = [event["step"] for event in events]
        self.assertIn("research_plan", steps)
        self.assertIn("observation_reflection", steps)
        self.assertIn("llm_decision", steps)
        self.assertIn("tool_call", steps)
        self.assertIn("tool_observation", steps)
        self.assertIn("final_report", steps)
        decision_events = [event for event in events if event["step"] == "llm_decision"]
        self.assertEqual(decision_events[0]["reasoning_summary"], "需要先读取当前持仓。")
        self.assertIn("raw_text", decision_events[0])
        plan_events = [event for event in events if event["step"] == "research_plan"]
        self.assertEqual(plan_events[0]["missing_capabilities"], ["缺少 ETF 底层持仓工具"])
        self.assertIn("research_plan", plan_events[0])
        observation_events = [event for event in events if event["step"] == "tool_observation"]
        self.assertIn("observation", observation_events[0])
        reflection_events = [event for event in events if event["step"] == "observation_reflection"]
        self.assertEqual(reflection_events[0]["observation_reflection"]["next_action"], "final_report")
        self.assertEqual(steps[-1], "done")
        self.assertEqual(events[-1]["snapshot"]["agent_report"]["summary"]["brief"], "组合可继续观察。")

    def test_tool_agent_stops_at_max_turns(self):
        config = self.config()
        config["agent"]["max_tool_turns"] = 1
        response = LlmToolStep(
            type="research_plan",
            research_plan={"information_needs": ["当前持仓"], "available_tool_mapping": [], "missing_capabilities": []},
            raw_text=json.dumps({"type": "research_plan", "research_plan": {"information_needs": ["当前持仓"]}}),
        )

        with patch("stock_assistant.agents.agent_loop.call_llm_tool_step", return_value=response):
            events = asyncio.run(
                collect_events(
                    config,
                    goal="分析当前持仓",
                    cached_results=[],
                    save_snapshot=False,
                    save_report=False,
                )
            )

        self.assertEqual(events[-1]["step"], "error")
        self.assertIn("max_tool_turns", events[-1]["error"])

    def test_tool_agent_pauses_with_checkpoint_on_llm_parse_failure(self):
        config = self.config()

        with patch("stock_assistant.agents.agent_loop.call_llm_tool_step", side_effect=ValueError("bad json")):
            events = asyncio.run(
                collect_events(
                    config,
                    goal="分析当前持仓",
                    cached_results=[],
                    save_snapshot=False,
                    save_report=False,
                )
            )

        self.assertEqual(events[-1]["step"], "paused")
        self.assertIn("checkpoint", events[-1])
        self.assertGreaterEqual(events[-1]["checkpoint"]["next_turn"], 2)
        self.assertIn("messages", events[-1]["checkpoint"])

    def test_tool_agent_requires_research_plan_first(self):
        config = self.config()
        response = LlmToolStep(
            type="tool_calls",
            tool_calls=[LlmToolCall(id="call_001", name="get_current_holdings", arguments={})],
            raw_text=json.dumps({"type": "tool_calls", "tool_calls": [{"name": "get_current_holdings"}]}),
        )

        with patch("stock_assistant.agents.agent_loop.call_llm_tool_step", return_value=response):
            events = asyncio.run(
                collect_events(
                    config,
                    goal="分析当前持仓",
                    cached_results=[],
                    save_snapshot=False,
                    save_report=False,
                )
            )

        self.assertEqual(events[-1]["step"], "error")
        self.assertIn("research_plan", events[-1]["error"])

    def test_tool_agent_requires_reflection_after_tool_observation(self):
        config = self.config()
        responses = [
            LlmToolStep(
                type="research_plan",
                research_plan={"information_needs": ["当前持仓"], "available_tool_mapping": [], "missing_capabilities": []},
                raw_text=json.dumps({"type": "research_plan", "research_plan": {"information_needs": ["当前持仓"]}}),
            ),
            LlmToolStep(
                type="tool_calls",
                tool_calls=[LlmToolCall(id="call_001", name="get_current_holdings", arguments={})],
                raw_text=json.dumps({"type": "tool_calls", "tool_calls": [{"name": "get_current_holdings"}]}),
            ),
            LlmToolStep(
                type="final_report",
                final_report={"summary": {"brief": "too soon"}},
                raw_text=json.dumps({"type": "final_report", "report": {"summary": {"brief": "too soon"}}}),
            ),
        ]

        with patch("stock_assistant.agents.agent_loop.call_llm_tool_step", side_effect=responses):
            events = asyncio.run(
                collect_events(
                    config,
                    goal="分析当前持仓",
                    cached_results=[],
                    save_snapshot=False,
                    save_report=False,
                )
            )

        self.assertEqual(events[-1]["step"], "paused")
        self.assertIn("observation_reflection", events[-1]["error"])
        self.assertIn("checkpoint", events[-1])

    def test_splits_oversized_technical_tool_calls(self):
        codes = [f"{index:06d}" for index in range(25)]
        calls = split_oversized_tool_calls([
            LlmToolCall(id="call_001", name="get_holding_technical", arguments={"codes": codes})
        ])

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[0].arguments["codes"]), 20)
        self.assertEqual(len(calls[1].arguments["codes"]), 5)

    def test_rejects_mid_run_research_plan(self):
        config = self.config()
        config["agent"]["max_tool_turns"] = 3
        responses = [
            LlmToolStep(
                type="research_plan",
                research_plan={"information_needs": ["当前持仓"], "available_tool_mapping": [], "missing_capabilities": []},
                raw_text=json.dumps({"type": "research_plan", "research_plan": {"information_needs": ["当前持仓"]}}),
            ),
            LlmToolStep(
                type="research_plan",
                research_plan={"information_needs": ["重新规划"], "available_tool_mapping": [], "missing_capabilities": []},
                raw_text=json.dumps({"type": "research_plan", "research_plan": {"information_needs": ["重新规划"]}}),
            ),
        ]

        with patch("stock_assistant.agents.agent_loop.call_llm_tool_step", side_effect=responses):
            events = asyncio.run(
                collect_events(
                    config,
                    goal="分析当前持仓",
                    cached_results=[],
                    save_snapshot=False,
                    save_report=False,
                )
            )

        repair_events = [event for event in events if event["step"] == "protocol_repair"]
        self.assertEqual(len(repair_events), 1)
        self.assertIn("research_plan", repair_events[0]["warning"])

    def test_merge_final_report_patch_appends_missing_holding_analysis(self):
        base = {
            "summary": {"brief": "base"},
            "holding_analysis": [{"target_code": "510300", "reason": "old"}],
            "limitations": ["缺底层持仓"],
        }
        patch_payload = {
            "holding_analysis": [{"target_code": "513130", "reason": "new"}],
            "limitations": ["缺底层持仓", "缺外部证据"],
        }

        merged = merge_final_report_patch(base, patch_payload)

        self.assertEqual([item["target_code"] for item in merged["holding_analysis"]], ["510300", "513130"])
        self.assertEqual(merged["limitations"], ["缺底层持仓", "缺外部证据"])

    def test_missing_technical_codes_requires_full_goal_coverage(self):
        workspace = AgentWorkspace(
            self.config(),
            holdings=[
                Holding(code="510300", name="沪深300ETF", market_value=1000, asset_type="etf"),
                Holding(code="513130", name="恒生科技", market_value=500, asset_type="etf"),
            ],
        )
        workspace._technical_by_code["510300"] = {  # noqa: SLF001
            "holding": {"code": "510300", "name": "沪深300ETF"},
            "ok": True,
            "action": "持有观察",
        }

        self.assertEqual(missing_technical_codes(workspace, "分析当前持仓"), [])
        self.assertEqual(missing_technical_codes(workspace, "分析每个 ETF 的建议"), ["513130"])

    def test_final_report_missing_holding_analysis_excludes_stock_like_etf_defaults(self):
        workspace = AgentWorkspace(
            self.config(),
            holdings=[
                Holding(code="512890", name="红利低波", market_value=1000, asset_type="etf"),
                Holding(code="600036", name="招商银行", market_value=500, asset_type="etf"),
                Holding(code="000259", name="农银区间收益混合", market_value=300, asset_type="fund"),
                Holding(code="007466", name="华泰柏瑞中证红利低波ETF联接A", market_value=200, asset_type="fund"),
                Holding(code="710301", name="富安达增强收益债券A", market_value=200, asset_type="etf"),
                Holding(code="021415", name="泰康中证红利低波动ETF联接A", market_value=200, asset_type="etf"),
            ],
        )
        report = {"holding_analysis": [{"target_code": "512890", "reason": "covered"}]}

        missing = final_report_missing_holding_analysis(workspace, "分析每个 ETF 的建议", report)

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
