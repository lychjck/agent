import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from stock_assistant.agents.agent_executor import execute_tool_call
from stock_assistant.agents.agent_tools import AgentToolSpec, build_agent_tool_registry
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.config import DEFAULT_CONFIG, load_config
from stock_assistant.core.llm_tools import LlmToolCall


SERVER_NAME = "stock_assistant_mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"
TOOL_PREFIX = "stock_"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
HTTP_DEFAULT_PATH = "/mcp"


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class StockMcpServer:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        max_observation_chars: int = 12000,
    ) -> None:
        self.config = config
        self.max_observation_chars = max_observation_chars
        self.registry = build_agent_tool_registry(config)
        self.workspace = AgentWorkspace(config)

    def external_tool_name(self, name: str) -> str:
        return f"{TOOL_PREFIX}{name}"

    def internal_tool_name(self, name: str) -> str:
        if not name.startswith(TOOL_PREFIX):
            raise JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"未知工具 {name}；本服务工具名必须以 {TOOL_PREFIX} 开头",
            )
        internal = name.removeprefix(TOOL_PREFIX)
        if internal not in self.registry:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"未知工具 {name}")
        return internal

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for tool in self.registry.values():
            if not tool.read_only:
                continue
            tools.append(self.mcp_tool_schema(tool))
        return tools

    def mcp_tool_schema(self, tool: AgentToolSpec) -> dict[str, Any]:
        return {
            "name": self.external_tool_name(tool.name),
            "description": tool.description,
            "inputSchema": tool.args_model.model_json_schema(),
            "annotations": {
                "title": self.external_tool_name(tool.name),
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        }

    def call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools/call 缺少 name")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools/call.arguments 必须是对象")

        internal_name = self.internal_tool_name(name)
        call = LlmToolCall(
            id=str(params.get("call_id") or f"mcp_{uuid.uuid4().hex[:12]}"),
            name=internal_name,
            arguments=arguments,
        )
        observation = execute_tool_call(
            call,
            self.registry,
            self.workspace,
            max_observation_chars=self.max_observation_chars,
        )
        payload = observation.model_dump()
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": not observation.ok,
        }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params", {})
        if request_id is None:
            return None
        if not isinstance(method, str):
            raise JsonRpcError(JSONRPC_INVALID_REQUEST, "JSON-RPC request 缺少 method")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "params 必须是对象")

        if method == "initialize":
            client_protocol = str(params.get("protocolVersion") or PROTOCOL_VERSION)
            return {
                "protocolVersion": client_protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.list_tools()}
        if method == "tools/call":
            return self.call_tool(params)
        if method == "shutdown":
            return {}
        raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"不支持 MCP method: {method}")


def jsonrpc_success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle_jsonrpc_message(server: StockMcpServer, message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    try:
        if message.get("jsonrpc") != "2.0":
            raise JsonRpcError(JSONRPC_INVALID_REQUEST, "jsonrpc 必须是 2.0")
        result = server.handle_request(message)
        if result is None:
            return None
        return jsonrpc_success(request_id, result)
    except JsonRpcError as exc:
        return jsonrpc_error(request_id, exc.code, exc.message, exc.data)
    except ValidationError as exc:
        return jsonrpc_error(request_id, JSONRPC_INVALID_PARAMS, str(exc))
    except Exception as exc:  # noqa: BLE001
        return jsonrpc_error(request_id, JSONRPC_INTERNAL_ERROR, str(exc))


def handle_jsonrpc_payload(server: StockMcpServer, payload: Any) -> Any:
    if isinstance(payload, list):
        responses: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                responses.append(jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "JSON-RPC batch item 必须是对象"))
                continue
            response = handle_jsonrpc_message(server, item)
            if response is not None:
                responses.append(response)
        return responses
    if not isinstance(payload, dict):
        return jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "JSON-RPC message 必须是对象")
    return handle_jsonrpc_message(server, payload)


def serve_stdio(server: StockMcpServer) -> None:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise JsonRpcError(JSONRPC_INVALID_REQUEST, "JSON-RPC message 必须是对象")
        except json.JSONDecodeError as exc:
            response = jsonrpc_error(None, JSONRPC_PARSE_ERROR, str(exc))
        except JsonRpcError as exc:
            response = jsonrpc_error(None, exc.code, exc.message, exc.data)
        else:
            response = handle_jsonrpc_message(server, message)

        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


def validate_bearer_token(authorization: str | None, expected_token: str) -> None:
    if not expected_token:
        return
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix(prefix).strip()
    if token != expected_token:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


def create_http_app(server: StockMcpServer, *, auth_token: str = "", mcp_path: str = HTTP_DEFAULT_PATH) -> FastAPI:
    app = FastAPI(title=SERVER_NAME, version=SERVER_VERSION)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "server": SERVER_NAME}

    @app.get(mcp_path)
    async def mcp_get(authorization: str | None = Header(default=None)) -> Response:
        validate_bearer_token(authorization, auth_token)
        return JSONResponse(
            status_code=405,
            content={
                "error": "This MCP server is stateless; send JSON-RPC requests with POST.",
                "server": SERVER_NAME,
            },
        )

    @app.post(mcp_path)
    async def mcp_post(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        validate_bearer_token(authorization, auth_token)
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            return JSONResponse(
                status_code=400,
                content=jsonrpc_error(None, JSONRPC_PARSE_ERROR, str(exc)),
            )
        response_payload = handle_jsonrpc_payload(server, payload)
        if response_payload is None or response_payload == []:
            return Response(status_code=202)
        return JSONResponse(content=response_payload)

    return app


def serve_http(
    server: StockMcpServer,
    *,
    host: str,
    port: int,
    mcp_path: str,
    auth_token: str,
) -> None:
    app = create_http_app(server, auth_token=auth_token, mcp_path=mcp_path)
    uvicorn.run(app, host=host, port=port)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run stock assistant MCP server.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.toml")
    parser.add_argument(
        "--max-observation-chars",
        type=int,
        default=12000,
        help="Maximum JSON characters returned in one tool observation",
    )
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio", help="MCP transport")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host")
    parser.add_argument("--port", type=int, default=8766, help="HTTP port")
    parser.add_argument("--path", default=HTTP_DEFAULT_PATH, help="HTTP MCP endpoint path")
    parser.add_argument("--auth-token", default="", help="Bearer token for HTTP MCP requests")
    parser.add_argument(
        "--auth-token-env",
        default="STOCK_MCP_TOKEN",
        help="Environment variable containing HTTP bearer token",
    )
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="Allow HTTP mode without bearer token",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = load_config(Path(args.config).expanduser())
    server = StockMcpServer(config, max_observation_chars=args.max_observation_chars)
    if args.transport == "stdio":
        serve_stdio(server)
        return 0

    auth_token = str(args.auth_token or os.environ.get(args.auth_token_env, "")).strip()
    if not auth_token and not args.allow_unauthenticated:
        sys.stderr.write(
            "HTTP MCP server requires --auth-token, "
            f"{args.auth_token_env}, or --allow-unauthenticated.\n"
        )
        return 2
    serve_http(
        server,
        host=str(args.host),
        port=int(args.port),
        mcp_path=str(args.path),
        auth_token=auth_token,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
