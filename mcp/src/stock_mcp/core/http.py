import json
import urllib.request
import urllib.parse
from typing import Any, Dict

class HttpClient:
    @staticmethod
    def request(
        url: str,
        method: str = "GET",
        headers: Dict[str, str] = None,
        data: Any = None,
        timeout: float = 30.0,
    ) -> str:
        headers = headers or {}
        req_data = None
        if data is not None:
            if isinstance(data, (dict, list)):
                req_data = json.dumps(data, ensure_ascii=False).encode("utf-8")
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json; charset=utf-8"
            elif isinstance(data, str):
                req_data = data.encode("utf-8")
            else:
                req_data = data

        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except Exception as e:
            raise RuntimeError(f"HTTP {method} to {url} failed: {e}")
