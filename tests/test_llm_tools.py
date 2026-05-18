import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from stock_assistant.core.config import DEFAULTS
from stock_assistant.core.llm import resolve_llm_config
from stock_assistant.core.llm_tools import call_llm_tool_step
from stock_assistant.core.llm_tools import parse_llm_tool_step


class TestLlmTools(unittest.TestCase):
    def test_resolve_configured_model_profile_switches_provider(self):
        config = deepcopy(DEFAULTS)
        config["llm"] = {
            **config["llm"],
            "base_url": "http://10.33.207.193:1234/v1",
            "model": "google/gemma-4-26b-a4b",
            "api_key_env": "",
            "stream": True,
            "model_profiles": {
                "deepseek-v4-pro": {
                    "client": "openai",
                    "base_url": "https://easyrouter.io/v1",
                    "model": "deepseek-v4-pro",
                    "api_key_env": "EASYROUTER_API_KEY",
                    "stream": False,
                }
            },
        }

        resolved = resolve_llm_config(config, "deepseek-v4-pro")
        llm = resolved["llm"]

        self.assertEqual(llm["base_url"], "https://easyrouter.io/v1")
        self.assertEqual(llm["model"], "deepseek-v4-pro")
        self.assertEqual(llm["api_key_env"], "EASYROUTER_API_KEY")
        self.assertFalse(llm["stream"])

    def test_resolve_unknown_model_override_keeps_default_provider(self):
        config = deepcopy(DEFAULTS)
        config["llm"] = {
            **config["llm"],
            "base_url": "http://10.33.207.193:1234/v1",
            "model": "google/gemma-4-26b-a4b",
            "model_profiles": {
                "deepseek-v4-pro": {
                    "base_url": "https://example.test/v1",
                    "model": "vendor/custom-deepseek",
                    "api_key_env": "CUSTOM_KEY",
                    "stream": True,
                }
            },
        }

        resolved = resolve_llm_config(config, "unconfigured/model")
        llm = resolved["llm"]

        self.assertEqual(llm["base_url"], "http://10.33.207.193:1234/v1")
        self.assertEqual(llm["model"], "unconfigured/model")

    def test_parse_research_plan(self):
        step = parse_llm_tool_step(json.dumps({
            "type": "research_plan",
            "reasoning_summary": "先规划信息需求。",
            "thinking_trace": {
                "information_needs": ["ETF 底层持仓"],
                "missing_capabilities": ["缺少 ETF 持仓工具"],
            },
            "research_plan": {
                "information_needs": ["ETF 底层持仓"],
                "available_tool_mapping": [{"need": "组合权重", "tool": "get_current_holdings"}],
                "missing_capabilities": ["缺少 ETF 持仓工具"],
            },
        }))

        self.assertEqual(step.type, "research_plan")
        self.assertEqual(step.reasoning_summary, "先规划信息需求。")
        self.assertEqual(step.research_plan["information_needs"], ["ETF 底层持仓"])
        self.assertEqual(step.thinking_trace["information_needs"], ["ETF 底层持仓"])
        self.assertEqual(step.missing_capabilities, ["缺少 ETF 持仓工具"])

    def test_parse_tool_calls(self):
        step = parse_llm_tool_step(json.dumps({
            "type": "tool_calls",
            "reasoning_summary": "先读取持仓。",
            "tool_calls": [
                {
                    "id": "call_001",
                    "name": "get_current_holdings",
                    "arguments": {"fields": ["code", "name"]},
                }
            ],
        }))

        self.assertEqual(step.type, "tool_calls")
        self.assertEqual(step.reasoning_summary, "先读取持仓。")
        self.assertEqual(step.tool_calls[0].name, "get_current_holdings")
        self.assertEqual(step.tool_calls[0].arguments["fields"], ["code", "name"])

    def test_parse_observation_reflection(self):
        step = parse_llm_tool_step(json.dumps({
            "type": "observation_reflection",
            "reasoning_summary": "工具返回后需要检查覆盖范围。",
            "thinking_trace": {
                "satisfied_needs": ["当前持仓"],
                "unsatisfied_needs": ["ETF 底层持仓"],
                "missing_capabilities": ["缺少 ETF 持仓工具"],
            },
            "observation_reflection": {
                "satisfied_needs": ["当前持仓"],
                "unsatisfied_needs": ["ETF 底层持仓"],
                "observation_impact": "已确定组合权重，但还不能穿透 ETF。",
                "next_action": "continue_tools",
            },
        }))

        self.assertEqual(step.type, "observation_reflection")
        self.assertEqual(step.observation_reflection["next_action"], "continue_tools")
        self.assertEqual(step.missing_capabilities, ["缺少 ETF 持仓工具"])

    def test_parse_observation_reflection_uses_thinking_trace_next_action(self):
        step = parse_llm_tool_step(json.dumps({
            "type": "observation_reflection",
            "reasoning_summary": "证据足够，可以生成报告。",
            "thinking_trace": {
                "known_facts": ["已覆盖主要标的"],
                "next_action": "final_report",
            },
        }))

        self.assertEqual(step.type, "observation_reflection")
        self.assertEqual(step.observation_reflection["next_action"], "final_report")

    def test_parse_markdown_final_report(self):
        step = parse_llm_tool_step("""```json
{"type":"final_report","reasoning_summary":"证据足够。","report":{"summary":{"brief":"ok"}}}
```""")

        self.assertEqual(step.type, "final_report")
        self.assertEqual(step.reasoning_summary, "证据足够。")
        self.assertEqual(step.final_report["summary"]["brief"], "ok")

    def test_parse_final_report_patch_as_final_report(self):
        step = parse_llm_tool_step(json.dumps({
            "type": "final_report_patch",
            "reasoning_summary": "补齐缺失标的。",
            "patch_content": {
                "holding_analysis": [
                    {"target_code": "512880", "target_name": "证券ETF", "action_type": "hold"}
                ]
            },
        }))

        self.assertEqual(step.type, "final_report")
        self.assertEqual(step.reasoning_summary, "补齐缺失标的。")
        self.assertEqual(step.final_report["holding_analysis"][0]["target_code"], "512880")

    def test_salvages_malformed_final_report_patch_as_empty_patch(self):
        step = parse_llm_tool_step(
            '---\n```json\n{"type":"final_report_patch", patch_content: {"holding_analysis": ['
        )

        self.assertEqual(step.type, "final_report")
        self.assertEqual(step.final_report["holding_analysis"], [])
        self.assertEqual(step.thinking_trace["recovery"], "salvaged_malformed_final_report_patch")

    def test_parse_strips_channel_markers(self):
        step = parse_llm_tool_step(
            '<|channel>thought\n<channel|>{"type":"observation_reflection",'
            '"reasoning_summary":"已反思",'
            '"observation_reflection":{"satisfied_needs":[],"unsatisfied_needs":[],"next_action":"final_report"}}'
        )

        self.assertEqual(step.type, "observation_reflection")
        self.assertEqual(step.reasoning_summary, "已反思")

    def test_parse_strips_frontmatter_channel_and_markdown(self):
        step = parse_llm_tool_step(
            '---\n<|channel>thought\n<channel|>```json\n'
            '{"type":"observation_reflection","reasoning_summary":"已采集本地数据",'
            '"observation_reflection":{"satisfied_needs":["本地数据"],"unsatisfied_needs":["外部研究"],'
            '"next_action":"continue_tools"}}\n```'
        )

        self.assertEqual(step.type, "observation_reflection")
        self.assertEqual(step.reasoning_summary, "已采集本地数据")

    def test_parse_strips_inline_channel_marker_with_replacement_char(self):
        step = parse_llm_tool_step(
            '<|channel>�{"type":"tool_calls","reasoning_summary":"继续查",'
            '"tool_calls":[{"name":"opencli_command","arguments":{"site":"eastmoney","command":"quote","positionals":["510300"]}}]}'
        )

        self.assertEqual(step.type, "tool_calls")
        self.assertEqual(step.tool_calls[0].name, "opencli_command")

    def test_salvages_malformed_observation_reflection(self):
        step = parse_llm_tool_step(
            '---\n<|channel>thought\n<channel|>```json\n'
            '{"type":"observation_reflection","reasoning_summary":"我已完成本地数据采集",'
            '"thinking_trace":{"known_facts":["权重 $\\\\ge 1\\\\%$ 的标的包括恒生科技 (8 '
        )

        self.assertEqual(step.type, "observation_reflection")
        self.assertIn("本地数据", step.reasoning_summary)
        self.assertEqual(step.observation_reflection["next_action"], "continue_tools")
        self.assertEqual(step.thinking_trace["recovery"], "salvaged_malformed_observation_reflection")

    def test_salvages_malformed_tool_calls_with_web_read_url(self):
        step = parse_llm_tool_step(
            '<|channel>thought\n<channel|>```json\n'
            '{"type":"tool_calls","reasoning_summary":"执行深度阅读",'
            '"tool_calls":[{"id":"call_011","name":"web_read","arguments":{'
            '"url":"https://zhuanlan.zhihu.com/p/197386827'
        )

        self.assertEqual(step.type, "tool_calls")
        self.assertEqual(step.reasoning_summary, "执行深度阅读")
        self.assertEqual(step.tool_calls[0].name, "web_read")
        self.assertEqual(step.tool_calls[0].arguments["url"], "https://zhuanlan.zhihu.com/p/197386827")
        self.assertEqual(step.thinking_trace["recovery"], "salvaged_malformed_tool_calls")

    def test_salvages_malformed_tool_calls_as_reflection_when_arguments_missing(self):
        step = parse_llm_tool_step(
            '<|channel>thought\n<channel|>{"type":"tool_calls","reasoning_summary":"继续调用工具","tool_calls":['
        )

        self.assertEqual(step.type, "observation_reflection")
        self.assertEqual(step.observation_reflection["next_action"], "continue_tools")
        self.assertEqual(step.thinking_trace["recovery"], "salvaged_malformed_tool_calls_as_reflection")

    def test_infers_single_tool_call(self):
        step = parse_llm_tool_step(json.dumps({
            "tool_call": {
                "name": "get_portfolio_profile",
                "arguments": {},
            }
        }))

        self.assertEqual(step.type, "tool_calls")
        self.assertEqual(step.tool_calls[0].id, "call_001")

    def test_rejects_tool_call_without_name(self):
        with self.assertRaises(ValueError):
            parse_llm_tool_step(json.dumps({"type": "tool_calls", "tool_calls": [{"arguments": {}}]}))

    @patch("stock_assistant.core.llm_tools.call_llm")
    def test_call_llm_tool_step_repairs_bad_protocol_once(self, call_llm):
        call_llm.side_effect = [
            "我需要先读取持仓。",
            json.dumps({
                "type": "tool_calls",
                "tool_calls": [{"name": "get_current_holdings", "arguments": {}}],
            }),
        ]
        config = deepcopy(DEFAULTS)

        step = call_llm_tool_step([{"role": "user", "content": "分析"}], [], config)

        self.assertEqual(step.type, "tool_calls")
        self.assertEqual(step.tool_calls[0].name, "get_current_holdings")
        self.assertEqual(call_llm.call_count, 2)


if __name__ == "__main__":
    unittest.main()
