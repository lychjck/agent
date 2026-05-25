from typing import Any

class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

class ProviderError(Exception):
    """外部数据源异常"""
    pass
