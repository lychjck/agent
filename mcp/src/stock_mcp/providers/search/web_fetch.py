from typing import Any, Dict
from stock_mcp.core.http import HttpClient

def web_fetch(url: str, timeout: float = 20.0) -> Dict[str, Any]:
    """轻量网页 HTML 获取"""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        content = HttpClient.request(url, method="GET", headers=headers, timeout=timeout)
        return {"ok": True, "content": content, "status_code": 200}
    except Exception as e:
        return {"ok": False, "error": str(e)}
