import pytest
from stock_mcp.server import McpServer, ToolContext

def test_ledger_placeholders_enabled(base_config):
    config = dict(base_config)
    config["mcp"]["expose_legacy_tzzb_placeholders"] = True
    server = McpServer(config)
    ctx = ToolContext(config, "test-req")
    
    res = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ctx)
    tool_names = {t["name"] for t in res["tools"]}
    assert "stock_get_trade_history" in tool_names
    assert "stock_get_daily_pnl" in tool_names

def test_ledger_placeholders_disabled(base_config):
    config = dict(base_config)
    config["mcp"]["expose_legacy_tzzb_placeholders"] = False
    server = McpServer(config)
    ctx = ToolContext(config, "test-req")
    
    res = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ctx)
    tool_names = {t["name"] for t in res["tools"]}
    assert "stock_get_trade_history" not in tool_names
    assert "stock_get_daily_pnl" not in tool_names
