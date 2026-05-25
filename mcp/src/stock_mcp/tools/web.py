from typing import Any, Dict, List
from pydantic import BaseModel, Field

from stock_mcp.registry import registry
from stock_mcp.context import ToolContext
from stock_mcp.providers.search.opencli import web_search as run_web_search, run_opencli_command
from stock_mcp.providers.search.web_read import web_read as run_web_read
from stock_mcp.providers.search.web_fetch import web_fetch as run_web_fetch

class WebSearchArgs(BaseModel):
    query: str = Field(..., description="搜索查询词")
    max_results: int = Field(5, description="最大结果数")

class WebReadArgs(BaseModel):
    url: str = Field(..., description="网页URL")

class WebFetchArgs(BaseModel):
    url: str = Field(..., description="HTML URL")

class OpenCliArgs(BaseModel):
    site: str = Field(..., description="命令名")
    command: str = Field(..., description="子命令")
    positionals: List[str] = Field(default_factory=list, description="位置参数")
    options: Dict[str, Any] = Field(default_factory=dict, description="选项参数")

@registry.register("web_search", "在 DuckDuckGo 搜索网络财经信息", WebSearchArgs)
def web_search(args: WebSearchArgs, ctx: ToolContext) -> dict:
    search_cfg = ctx.config.get("search", {})
    timeout = float(search_cfg.get("timeout_seconds", 20))
    res = run_web_search(args.query, args.max_results, timeout=timeout)
    return {"ok": True, "results": res}

@registry.register("web_read", "抓取给定网页并将其转化为 Markdown", WebReadArgs)
def web_read(args: WebReadArgs, ctx: ToolContext) -> dict:
    search_cfg = ctx.config.get("search", {})
    timeout = float(search_cfg.get("timeout_seconds", 20))
    res = run_web_read(args.url, timeout=timeout)
    return res

@registry.register("web_fetch", "轻量级请求网页 HTML", WebFetchArgs)
def web_fetch(args: WebFetchArgs, ctx: ToolContext) -> dict:
    search_cfg = ctx.config.get("search", {})
    timeout = float(search_cfg.get("timeout_seconds", 20))
    res = run_web_fetch(args.url, timeout=timeout)
    return res

@registry.register("opencli_command", "直通 opencli 执行原始财经指令", OpenCliArgs)
def opencli_command(args: OpenCliArgs, ctx: ToolContext) -> dict:
    search_cfg = ctx.config.get("search", {})
    timeout = float(search_cfg.get("timeout_seconds", 20))
    res = run_opencli_command(args.site, args.command, args.positionals, args.options, timeout=timeout)
    return res
