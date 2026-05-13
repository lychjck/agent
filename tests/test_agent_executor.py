import unittest
from copy import deepcopy

from stock_assistant.agent_executor import execute_tool_call
from stock_assistant.agent_tools import build_agent_tool_registry
from stock_assistant.agent_workspace import AgentWorkspace
from stock_assistant.config import DEFAULTS
from stock_assistant.llm_tools import LlmToolCall
from stock_assistant.models import Holding


class TestAgentExecutor(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(DEFAULTS)
        self.workspace = AgentWorkspace(
            self.config,
            holdings=[Holding(code="510300", name="沪深300ETF", market_value=1000, asset_type="etf")],
        )
        self.registry = build_agent_tool_registry(self.config)

    def test_unknown_tool_returns_observation_error(self):
        observation = execute_tool_call(LlmToolCall(id="c1", name="read_cookie", arguments={}), self.registry, self.workspace)

        self.assertFalse(observation.ok)
        self.assertEqual(observation.error_type, "unknown_tool")

    def test_invalid_arguments_returns_observation_error(self):
        observation = execute_tool_call(
            LlmToolCall(id="c1", name="get_holding_technical", arguments={"codes": []}),
            self.registry,
            self.workspace,
        )

        self.assertFalse(observation.ok)
        self.assertEqual(observation.error_type, "invalid_arguments")

    def test_successful_tool_call_redacts_to_allowed_fields(self):
        observation = execute_tool_call(
            LlmToolCall(id="c1", name="get_current_holdings", arguments={"fields": ["code", "name", "cookie"]}),
            self.registry,
            self.workspace,
        )

        self.assertTrue(observation.ok)
        self.assertEqual(set(observation.result["holdings"][0]), {"code", "name"})


if __name__ == "__main__":
    unittest.main()
