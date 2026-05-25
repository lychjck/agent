import json
import sys

from stock_mcp.server.handler import McpServer, handle_jsonrpc_payload
from stock_mcp.server.jsonrpc import JSONRPC_PARSE_ERROR, JSONRPC_INTERNAL_ERROR, jsonrpc_error

def serve_stdio(server: McpServer) -> None:
    """Stdio 传输层实现"""
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
            response = handle_jsonrpc_payload(server, message)
        except json.JSONDecodeError as exc:
            response = jsonrpc_error(None, JSONRPC_PARSE_ERROR, str(exc))
        except Exception as exc:
            response = jsonrpc_error(None, JSONRPC_INTERNAL_ERROR, str(exc))
        
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
