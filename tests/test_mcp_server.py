import json
import unittest
from copy import deepcopy

from stock_assistant.core.config import DEFAULTS
from stock_assistant.core.models import Holding
from stock_assistant.mcp_server import StockMcpServer, create_http_app, handle_jsonrpc_message, handle_jsonrpc_payload

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


class TestStockMcpServer(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(DEFAULTS)
        self.server = StockMcpServer(self.config)
        self.server.workspace._holdings = [
            Holding(code="510300", name="沪深300ETF", market_value=1000, profit_pct=5.0, asset_type="etf"),
            Holding(code="511880", name="货币ETF", market_value=500, profit_pct=1.0, asset_type="etf"),
        ]

    def test_initialize_returns_tools_capability(self):
        response = handle_jsonrpc_message(
            self.server,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
        )

        self.assertEqual(response["result"]["capabilities"], {"tools": {}})
        self.assertEqual(response["result"]["serverInfo"]["name"], "stock_assistant_mcp")

    def test_tools_list_exposes_prefixed_read_only_tools(self):
        response = handle_jsonrpc_message(
            self.server,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

        tools = response["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertIn("stock_get_current_holdings", names)
        self.assertTrue(all(name.startswith("stock_") for name in names))
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tools))
        self.assertTrue(all("inputSchema" in tool for tool in tools))

    def test_tools_call_returns_structured_observation(self):
        response = handle_jsonrpc_message(
            self.server,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "stock_get_current_holdings",
                    "arguments": {"fields": ["code", "name", "weight"]},
                },
            },
        )

        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertTrue(result["structuredContent"]["ok"])
        self.assertEqual(result["structuredContent"]["tool_name"], "get_current_holdings")
        self.assertEqual(set(result["structuredContent"]["result"]["holdings"][0]), {"code", "name", "weight_pct"})
        content_payload = json.loads(result["content"][0]["text"])
        self.assertEqual(content_payload["summary"], "返回 2 只持仓")

    def test_tools_call_rejects_unprefixed_tool_name(self):
        response = handle_jsonrpc_message(
            self.server,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "get_current_holdings", "arguments": {}},
            },
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("stock_", response["error"]["message"])

    def test_tools_call_returns_tool_error_for_invalid_arguments(self):
        response = handle_jsonrpc_message(
            self.server,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "stock_get_holding_technical", "arguments": {"codes": []}},
            },
        )

        self.assertFalse(response["result"]["structuredContent"]["ok"])
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["error_type"], "invalid_arguments")

    def test_batch_payload_handles_multiple_requests(self):
        response = handle_jsonrpc_payload(
            self.server,
            [
                {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ],
        )

        self.assertEqual(len(response), 2)
        self.assertEqual(response[0]["result"], {})
        self.assertIn("tools", response[1]["result"])

    @unittest.skipIf(TestClient is None, "fastapi TestClient is unavailable")
    def test_http_mcp_requires_bearer_token(self):
        app = create_http_app(self.server, auth_token="secret")
        client = TestClient(app)

        response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})

        self.assertEqual(response.status_code, 401)

    @unittest.skipIf(TestClient is None, "fastapi TestClient is unavailable")
    def test_http_mcp_lists_tools_with_bearer_token(self):
        app = create_http_app(self.server, auth_token="secret")
        client = TestClient(app)

        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer secret"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

        self.assertEqual(response.status_code, 200)
        names = {tool["name"] for tool in response.json()["result"]["tools"]}
        self.assertIn("stock_get_current_holdings", names)


if __name__ == "__main__":
    unittest.main()
