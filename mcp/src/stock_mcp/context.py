from typing import Any, Dict

class ToolContext:
    """工具调用的生命周期上下文，单次调用隔离"""
    def __init__(self, config: Dict[str, Any], request_id: str) -> None:
        self.config = config
        self.request_id = request_id
        self._kline_cache: Dict[str, Any] = {}

    def get_cached_kline(self, code: str) -> Any | None:
        return self._kline_cache.get(code)

    def set_cached_kline(self, code: str, kline: Any) -> None:
        self._kline_cache[code] = kline
