import json
import select
import subprocess
import time
from typing import Any


class McpProtocolError(RuntimeError):
    pass


def mcp_server_config(config: dict[str, Any], server_name: str) -> dict[str, Any]:
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        return {}
    server = servers.get(server_name, {})
    return server if isinstance(server, dict) else {}


def write_mcp_message(process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise McpProtocolError("MCP server stdin is not available")
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def read_mcp_response(process: subprocess.Popen[str], request_id: int, timeout_seconds: int) -> dict[str, Any]:
    if process.stdout is None:
        raise McpProtocolError("MCP server stdout is not available")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        readable, _, _ = select.select([process.stdout], [], [], remaining)
        if not readable:
            continue
        line = process.stdout.readline()
        if not line:
            break
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("id") != request_id:
            continue
        if payload.get("error"):
            raise McpProtocolError(str(payload["error"]))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError(f"MCP response {request_id} missing object result")
        return result
    stderr = ""
    if process.poll() is not None and process.stderr is not None:
        stderr = process.stderr.read().strip()
    detail = f"; stderr={stderr}" if stderr else ""
    raise TimeoutError(f"MCP request {request_id} timed out after {timeout_seconds}s{detail}")


def choose_mcp_tool(tools: list[dict[str, Any]], configured_name: str) -> str:
    names = [str(tool.get("name", "")) for tool in tools if isinstance(tool, dict)]
    if configured_name in names:
        return configured_name
    if configured_name and configured_name != "auto":
        search_like = [name for name in names if "search" in name.lower()]
        if search_like:
            return search_like[0]
        raise McpProtocolError(f"MCP tool {configured_name} not found; available={names}")
    for name in names:
        if "search" in name.lower():
            return name
    raise McpProtocolError(f"No search tool found in MCP server; available={names}")


def mcp_call_tool(
    *,
    command: str,
    args: list[str],
    tool_name: str,
    arguments: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    process = subprocess.Popen(
        [command, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        write_mcp_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "stock-assistant", "version": "0.1.0"},
                },
            },
        )
        read_mcp_response(process, 1, timeout_seconds)
        write_mcp_message(
            process,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        write_mcp_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        tools_result = read_mcp_response(process, 2, timeout_seconds)
        tools = tools_result.get("tools", [])
        if not isinstance(tools, list):
            raise McpProtocolError("MCP tools/list result missing tools array")
        actual_tool_name = choose_mcp_tool(tools, tool_name)
        write_mcp_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": actual_tool_name, "arguments": arguments},
            },
        )
        result = read_mcp_response(process, 3, timeout_seconds)
        result["_tool_name"] = actual_tool_name
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def parse_mcp_content(result: dict[str, Any]) -> list[dict[str, Any]]:
    content = result.get("content", [])
    if not isinstance(content, list):
        return [{"type": "raw", "text": str(content)}]
    parsed: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            parsed.append({"type": "raw", "text": str(item)})
            continue
        text = item.get("text")
        if isinstance(text, str):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                parsed.extend(decoded if all(isinstance(row, dict) for row in decoded) else [{"text": text}])
            elif isinstance(decoded, dict):
                parsed.append(decoded)
            else:
                parsed.append({"type": item.get("type", "text"), "text": text})
        else:
            parsed.append(item)
    return parsed


def mcp_search_unavailable(results: list[dict[str, Any]]) -> bool:
    if not results:
        return True
    combined = "\n".join(str(item.get("text", "")) for item in results if isinstance(item, dict)).lower()
    return (
        "no results were found" in combined
        and ("bot detection" in combined or "try rephrasing" in combined)
    )


def run_mcp_search(config: dict[str, Any], query: str, max_results: int) -> dict[str, Any]:
    search_config = config.get("search", {})
    mcp_config = search_config.get("mcp", {}) if isinstance(search_config, dict) else {}
    server_name = str(mcp_config.get("server", "ddg-search"))
    server = mcp_server_config(config, server_name)
    command = str(server.get("command", "")).strip()
    args = server.get("args", [])
    if not command:
        raise RuntimeError(f"MCP server {server_name} missing command")
    if not isinstance(args, list):
        raise RuntimeError(f"MCP server {server_name} args must be a list")

    tool_name = str(mcp_config.get("tool_name", "search"))
    timeout_seconds = int(mcp_config.get("timeout_seconds", search_config.get("timeout_seconds", 45)))
    region = str(mcp_config.get("region", "")).strip()
    arguments: dict[str, Any] = {"query": query, "max_results": max_results}
    if region:
        arguments["region"] = region
    result = mcp_call_tool(
        command=command,
        args=[str(item) for item in args],
        tool_name=tool_name,
        arguments=arguments,
        timeout_seconds=timeout_seconds,
    )
    results = parse_mcp_content(result)
    return {
        "server": server_name,
        "tool": str(result.get("_tool_name") or tool_name),
        "results": results,
        "is_error": bool(result.get("isError")),
        "available": not mcp_search_unavailable(results),
    }
