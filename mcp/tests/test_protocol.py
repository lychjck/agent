from stock_mcp.server import McpServer, ToolContext
from stock_mcp.core.config import load_config
import pytest

def test_mcp_server_initialize(base_config):
    server = McpServer(base_config)
    ctx = ToolContext(base_config, "test-req")
    
    # 模拟 initialize
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    res = server.handle_request(req, ctx)
    assert res is not None
    assert "protocolVersion" in res
    assert res["serverInfo"]["name"] == "stock_mcp"
    
    # 模拟 tools/list
    req_list = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    res_list = server.handle_request(req_list, ctx)
    assert res_list is not None
    tools = {t["name"] for t in res_list["tools"]}
    assert "stock_get_current_account_bundle" in tools
    assert "stock_get_holding_technical" in tools
    assert "stock_save_snapshot" in tools

def test_load_config_validation(tmp_path):
    # 1. 找不到文件直接抛 FileNotFoundError
    with pytest.raises(FileNotFoundError) as excinfo:
        load_config(tmp_path / "nonexistent.toml")
    assert "未找到配置文件" in str(excinfo.value)
    
    # 2. 存在文件但缺失关键参数报错
    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("[mcp]\nlog_level = 'INFO'\n# 缺少 expose_legacy_tzzb_placeholders", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        load_config(bad_toml)
    assert "配置文件检测失败" in str(excinfo.value)
    assert "缺失必要配置项: [mcp] expose_legacy_tzzb_placeholders" in str(excinfo.value)

