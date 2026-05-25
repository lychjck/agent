import json
import uuid
from typing import Any, Dict, List
from pydantic import ValidationError

from stock_mcp.core.logging import logger
from stock_mcp.core.errors import McpError
from stock_mcp.context import ToolContext
from stock_mcp.registry import registry
import stock_mcp.tools
from stock_mcp.server.jsonrpc import (
    JSONRPC_INVALID_REQUEST,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_INTERNAL_ERROR,
    jsonrpc_success,
    jsonrpc_error,
)

class McpServer:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    def handle_request(self, request: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params", {})
        
        if request_id is None:
            return None  # 忽略 notification
        if not isinstance(method, str):
            raise McpError(JSONRPC_INVALID_REQUEST, "JSON-RPC request 缺少 method")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise McpError(JSONRPC_INVALID_PARAMS, "params 必须是对象")

        if method == "initialize":
            return {
                "protocolVersion": str(params.get("protocolVersion") or "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stock_mcp", "version": "0.1.0"},
            }
        elif method == "ping":
            return {}
        elif method == "tools/list":
            tools_list = []
            expose_placeholders = self.config.get("mcp", {}).get("expose_legacy_tzzb_placeholders", True)
            placeholders = {"stock_get_trade_history", "stock_get_daily_pnl", "stock_get_monthly_pnl", "stock_get_yearly_pnl"}
            for tool in registry.tools.values():
                if not expose_placeholders and tool.external_name in placeholders:
                    continue
                tools_list.append({
                    "name": tool.external_name,
                    "description": tool.description,
                    "inputSchema": tool.args_schema.model_json_schema(),
                })
            return {"tools": tools_list}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if arguments is None:
                arguments = {}
            if not isinstance(name, str) or not name.strip():
                raise McpError(JSONRPC_INVALID_PARAMS, "tools/call 缺少 name")
            if not isinstance(arguments, dict):
                raise McpError(JSONRPC_INVALID_PARAMS, "arguments 必须 be object")
            
            expose_placeholders = self.config.get("mcp", {}).get("expose_legacy_tzzb_placeholders", True)
            placeholders = {"stock_get_trade_history", "stock_get_daily_pnl", "stock_get_monthly_pnl", "stock_get_yearly_pnl"}
            if not expose_placeholders and name in placeholders:
                raise McpError(JSONRPC_INVALID_PARAMS, f"未知工具: {name}")
                
            tool = registry.tools.get(name)
            if not tool:
                raise McpError(JSONRPC_INVALID_PARAMS, f"未知工具: {name}")
            
            try:
                args = tool.args_schema.model_validate(arguments)
            except ValidationError as e:
                raise McpError(JSONRPC_INVALID_PARAMS, f"参数验证失败: {str(e)}")
            
            try:
                res = tool.handler(args, ctx)
                text = json.dumps(res, ensure_ascii=False)
                return {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": res,
                    "isError": not res.get("ok", True),
                }
            except Exception as e:
                logger.error(f"Error executing tool {name}: {e}", exc_info=True)
                return {
                    "content": [{"type": "text", "text": json.dumps({"ok": False, "error_type": "internal_error", "message": str(e)}, ensure_ascii=False)}],
                    "structuredContent": {"ok": False, "error_type": "internal_error", "message": str(e)},
                    "isError": True,
                }
        elif method == "shutdown":
            return {}
        else:
            raise McpError(JSONRPC_METHOD_NOT_FOUND, f"不支持的 MCP 方法: {method}")

def handle_jsonrpc_message(server: McpServer, message: Dict[str, Any]) -> Dict[str, Any] | None:
    request_id = message.get("id")
    ctx = ToolContext(server.config, str(request_id or uuid.uuid4().hex[:12]))
    try:
        if message.get("jsonrpc") != "2.0":
            raise McpError(JSONRPC_INVALID_REQUEST, "jsonrpc 必须是 2.0")
        result = server.handle_request(message, ctx)
        if result is None:
            return None
        return jsonrpc_success(request_id, result)
    except McpError as exc:
        return jsonrpc_error(request_id, exc.code, exc.message, exc.data)
    except Exception as exc:
        return jsonrpc_error(request_id, JSONRPC_INTERNAL_ERROR, str(exc))

def handle_jsonrpc_payload(server: McpServer, payload: Any) -> Any:
    if isinstance(payload, list):
        responses = []
        for item in payload:
            if not isinstance(item, dict):
                responses.append(jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Batch item 必须是对象"))
                continue
            res = handle_jsonrpc_message(server, item)
            if res is not None:
                responses.append(res)
        return responses
    if not isinstance(payload, dict):
        return jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Message 必须是对象")
    return handle_jsonrpc_message(server, payload)
