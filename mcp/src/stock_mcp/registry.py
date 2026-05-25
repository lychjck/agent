from typing import Any, Callable, Dict
from pydantic import BaseModel

from stock_mcp.context import ToolContext

class ToolSpec:
    """MCP 工具规格"""
    def __init__(
        self,
        name: str,
        description: str,
        args_schema: type[BaseModel],
        handler: Callable[[Any, ToolContext], Dict[str, Any]],
        external_name: str = "",
    ) -> None:
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self.handler = handler
        self.external_name = external_name or f"stock_{name}"

class ToolRegistry:
    """MCP 工具注册表"""
    def __init__(self) -> None:
        self.tools: Dict[str, ToolSpec] = {}

    def register(self, name: str, description: str, args_schema: type[BaseModel], external_name: str = "") -> Callable:
        def decorator(handler: Callable[[Any, ToolContext], Dict[str, Any]]) -> Callable:
            spec = ToolSpec(name, description, args_schema, handler, external_name)
            self.tools[spec.external_name] = spec
            return handler
        return decorator

registry = ToolRegistry()
