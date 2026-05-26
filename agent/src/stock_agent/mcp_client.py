"""MCP HTTP 客户端 - 调用 stock_mcp 服务"""

import json
from typing import Any

import httpx


class McpClient:
    """通过 HTTP JSON-RPC 调用 MCP 服务"""

    def __init__(self, url: str, token: str = "", timeout: float = 30.0):
        self.url = url
        self.token = token
        self.timeout = timeout
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用 MCP 工具，返回结果"""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {},
            },
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.url, json=payload, headers=self._headers())
            resp.raise_for_status()

        data = resp.json()
        if "error" in data:
            return {"ok": False, "error": data["error"]}

        result = data.get("result", {})
        # MCP 返回格式: {content: [{type: "text", text: "..."}], structuredContent: {...}}
        structured = result.get("structuredContent")
        if structured:
            return structured

        # 降级：从 text content 解析
        content = result.get("content", [])
        if content and isinstance(content, list):
            text = content[0].get("text", "{}")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"ok": True, "raw": text}

        return {"ok": True, "result": result}

    def list_tools(self) -> list[dict[str, Any]]:
        """列出所有可用工具"""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.url, json=payload, headers=self._headers())
            resp.raise_for_status()

        data = resp.json()
        return data.get("result", {}).get("tools", [])

    # ===== 便捷方法 =====

    def get_account_bundle(self) -> dict[str, Any]:
        """一键获取持仓 + 画像 + 分类"""
        return self.call_tool("stock_get_current_account_bundle")

    def get_technical(self, codes: list[str], lookback_days: int = 120) -> dict[str, Any]:
        """获取技术指标"""
        return self.call_tool("stock_get_holding_technical", {
            "codes": codes,
            "lookback_days": lookback_days,
        })

    def get_etf_constituents(self, codes: list[str]) -> dict[str, Any]:
        """获取 ETF 重仓股"""
        return self.call_tool("stock_get_etf_constituents", {"codes": codes})

    def web_search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """搜索财经信息"""
        return self.call_tool("stock_web_search", {
            "query": query,
            "max_results": max_results,
        })

    def get_fund_performance(self, code: str) -> dict[str, Any]:
        """获取基金绩效指标"""
        return self.call_tool("stock_get_fund_performance_metrics", {"code": code})

    def save_snapshot(self, data: dict[str, Any]) -> dict[str, Any]:
        """保存诊断快照"""
        return self.call_tool("stock_save_snapshot", {"snapshot_data": data})

    def compare_snapshots(self, current_facts: dict[str, Any]) -> dict[str, Any]:
        """对比历史快照"""
        return self.call_tool("stock_compare_snapshots", {"current_facts": current_facts})
