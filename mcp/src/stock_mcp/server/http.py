import json
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from stock_mcp.server.handler import McpServer, handle_jsonrpc_payload
from stock_mcp.server.jsonrpc import JSONRPC_PARSE_ERROR, jsonrpc_error

def create_http_app(server: McpServer, auth_token: str = "", mcp_path: str = "/mcp") -> FastAPI:
    """FastAPI HTTP 传输层实现"""
    app = FastAPI(title="stock_mcp", version="0.1.0")

    def validate_bearer_token(authorization: str | None) -> None:
        if not auth_token:
            return
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization.removeprefix(prefix).strip()
        if token != auth_token:
            raise HTTPException(status_code=403, detail="Invalid bearer token")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "server": "stock_mcp"}

    @app.post(mcp_path)
    async def mcp_post(request: Request, authorization: str | None = Header(default=None)) -> Response:
        validate_bearer_token(authorization)
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            return JSONResponse(status_code=400, content=jsonrpc_error(None, JSONRPC_PARSE_ERROR, str(exc)))
        
        response_payload = handle_jsonrpc_payload(server, payload)
        if response_payload is None or response_payload == []:
            return Response(status_code=202)
        return JSONResponse(content=response_payload)

    return app
