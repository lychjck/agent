import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stock_assistant.agents.agent_llm import classification_record, technical_record
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.models import holding_to_dict
from stock_assistant.core.skills import (
    fetch_url_bytes,
    list_installed_skills,
    read_skill_content,
    read_skill_file_content,
    skill_file_paths,
    strip_html_tags,
    web_search_results,
)
from stock_assistant.core.utils import config_bool


ALLOWED_HOLDING_FIELDS = {
    "code",
    "name",
    "asset_type",
    "market_value",
    "weight_pct",
    "profit_pct",
    "hold_profit",
    "day_profit",
}

HOLDING_FIELD_ALIASES = {
    "weight": "weight_pct",
    "position_weight": "weight_pct",
    "portfolio_weight": "weight_pct",
    "pnl": "profit_pct",
    "return": "profit_pct",
    "return_pct": "profit_pct",
    "profit": "profit_pct",
    "value": "market_value",
    "marketValue": "market_value",
}


class AgentToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel, AgentWorkspace], dict[str, Any]]
    permission: str = "portfolio:read"
    read_only: bool = True


class GetCurrentHoldingsArgs(BaseModel):
    fields: list[str] = Field(default_factory=list)


class GetHoldingTechnicalArgs(BaseModel):
    codes: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    lookback_days: int = Field(default=120, ge=20, le=250)


class GetClassificationArgs(BaseModel):
    codes: list[str] = Field(default_factory=list, min_length=1, max_length=50)


class GetPortfolioProfileArgs(BaseModel):
    include: list[str] = Field(default_factory=list)


class LoadSnapshotSummaryArgs(BaseModel):
    which: Literal["latest"] = "latest"


class CompareSnapshotsArgs(BaseModel):
    current: Literal["workspace"] = "workspace"
    previous: Literal["latest"] = "latest"


class GenerateCandidateActionsArgs(BaseModel):
    codes: list[str] = Field(default_factory=list, max_length=50)


class ListSkillsArgs(BaseModel):
    query: str = ""


class ReadSkillArgs(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ListSkillFilesArgs(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ReadSkillFileArgs(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=240)


class WebFetchArgs(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    max_chars: int = Field(default=8000, ge=500, le=20000)


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    engines: list[str] = Field(default_factory=list, max_length=4)
    max_results: int = Field(default=5, ge=1, le=10)


class WebReadArgs(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    max_chars: int = Field(default=12000, ge=1000, le=30000)


def holding_tool_record(holding: Any, total_value: float | None, fields: list[str]) -> dict[str, Any]:
    record = holding_to_dict(holding)
    record["weight_pct"] = (
        holding.market_value / total_value * 100
        if holding.market_value is not None and total_value
        else None
    )
    selected_fields = fields or ["code", "name", "asset_type", "market_value", "weight_pct", "profit_pct"]
    return {
        key: record.get(key)
        for key in selected_fields
        if key in ALLOWED_HOLDING_FIELDS
    }


def normalize_holding_fields(fields: list[str]) -> list[str]:
    normalized: list[str] = []
    for field in fields:
        key = HOLDING_FIELD_ALIASES.get(str(field), str(field))
        if key in ALLOWED_HOLDING_FIELDS and key not in normalized:
            normalized.append(key)
    return normalized


def ensure_codes_in_holdings(codes: list[str], workspace: AgentWorkspace) -> list[str]:
    holding_codes = {holding.code for holding in workspace.ensure_holdings()}
    missing = [code for code in codes if code not in holding_codes]
    if missing:
        raise ValueError(f"请求的标的不在当前持仓中: {', '.join(missing)}")
    return codes


def handle_get_current_holdings(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, GetCurrentHoldingsArgs) else GetCurrentHoldingsArgs.model_validate(args)
    fields = normalize_holding_fields(typed.fields)
    holdings = workspace.ensure_holdings()
    total_value = workspace.total_value()
    return {
        "count": len(holdings),
        "holdings": [holding_tool_record(holding, total_value, fields) for holding in holdings],
        "summary": f"返回 {len(holdings)} 只持仓",
    }


def handle_get_portfolio_profile(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, GetPortfolioProfileArgs) else GetPortfolioProfileArgs.model_validate(args)
    profile = workspace.ensure_portfolio_profile()
    requested = {str(item).strip() for item in typed.include if str(item).strip()}
    include_positions = not requested or any(item in requested for item in {"positions", "top_positions"})
    portfolio = {
        "total_value": profile.get("total_value"),
        "position_count": profile.get("position_count"),
        "by_asset_class": profile.get("by_asset_class", {}),
        "by_sector": profile.get("by_sector", {}),
        "by_theme": profile.get("by_theme", {}),
        "by_strategy": profile.get("by_strategy", {}),
        "by_region": profile.get("by_region", {}),
        "by_asset_type": profile.get("by_asset_type", {}),
        "unknown_classification_pct": profile.get("unknown_classification_pct"),
        "low_confidence_classification_pct": profile.get("low_confidence_classification_pct"),
        "top_positions": profile.get("top_positions", []),
    }
    if include_positions:
        portfolio["positions"] = profile.get("positions", [])
    payload = {
        "portfolio": portfolio,
        "observations": workspace.observations,
    }
    if typed.include:
        allowed = requested | {"observations", "portfolio"}
        payload = {key: value for key, value in payload.items() if key in allowed}
    payload["summary"] = (
        f"组合画像包含 {profile.get('position_count', 0)} 个标的，"
        f"资产大类 {len(profile.get('by_asset_class', {}) or {})} 类"
    )
    return payload


def handle_get_holding_technical(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, GetHoldingTechnicalArgs) else GetHoldingTechnicalArgs.model_validate(args)
    codes = ensure_codes_in_holdings(typed.codes, workspace)
    results = workspace.ensure_technical(codes)
    technical: dict[str, Any] = {}
    for item in results:
        holding = item.get("holding", {})
        code = str(holding.get("code", ""))
        if code:
            technical[code] = technical_record(item)
    return {
        "technical": technical,
        "summary": f"返回 {len(technical)} 个标的技术指标",
    }


def handle_get_classification(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, GetClassificationArgs) else GetClassificationArgs.model_validate(args)
    codes = ensure_codes_in_holdings(typed.codes, workspace)
    classifications = workspace.ensure_classifications()
    return {
        "classifications": {
            code: classification_record(classifications.get(code))
            for code in codes
        },
        "summary": f"返回 {len(codes)} 个标的分类",
    }


def snapshot_summary(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not snapshot:
        return {"exists": False}
    return {
        "exists": True,
        "generated_at": snapshot.get("generated_at"),
        "source": snapshot.get("source"),
        "model": snapshot.get("model"),
        "portfolio": {
            "total_value": snapshot.get("portfolio", {}).get("total_value"),
            "position_count": snapshot.get("portfolio", {}).get("position_count"),
            "top_positions": snapshot.get("portfolio", {}).get("top_positions", []),
        },
        "risk_count": len(snapshot.get("risk_flags", []) or []),
        "action_count": len(snapshot.get("candidate_actions", []) or []),
    }


def handle_load_snapshot_summary(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    _ = args if isinstance(args, LoadSnapshotSummaryArgs) else LoadSnapshotSummaryArgs.model_validate(args)
    summary = snapshot_summary(workspace.previous_snapshot())
    summary["summary"] = "返回最新历史快照摘要" if summary.get("exists") else "没有历史快照"
    return summary


def handle_compare_snapshots(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    _ = args if isinstance(args, CompareSnapshotsArgs) else CompareSnapshotsArgs.model_validate(args)
    diff = workspace.ensure_history_diff()
    return {
        "diff": diff,
        "summary": "当前事实数据与上一份快照一致" if diff.get("duplicate_of_latest") else "返回历史快照对比",
    }


def handle_generate_candidate_actions(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    _ = args if isinstance(args, GenerateCandidateActionsArgs) else GenerateCandidateActionsArgs.model_validate(args)
    return {
        "available": False,
        "candidate_actions": [],
        "summary": "候选动作工具需要 policy 规则确认后启用",
    }


def handle_list_skills(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, ListSkillsArgs) else ListSkillsArgs.model_validate(args)
    query = typed.query.strip().lower()
    records = [record.to_dict() for record in list_installed_skills(workspace.config)]
    if query:
        records = [
            record for record in records
            if query in record.get("name", "").lower() or query in record.get("description", "").lower()
        ]
    return {
        "count": len(records),
        "skills": records,
        "summary": f"返回 {len(records)} 个可用 skill",
    }


def handle_read_skill(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, ReadSkillArgs) else ReadSkillArgs.model_validate(args)
    return read_skill_content(workspace.config, typed.name)


def handle_list_skill_files(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, ListSkillFilesArgs) else ListSkillFilesArgs.model_validate(args)
    files = skill_file_paths(workspace.config, typed.name)
    return {
        "count": len(files),
        "files": files,
        "summary": f"返回 {len(files)} 个 skill 文件",
    }


def handle_read_skill_file(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, ReadSkillFileArgs) else ReadSkillFileArgs.model_validate(args)
    return read_skill_file_content(workspace.config, typed.name, typed.path)


def handle_web_fetch(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, WebFetchArgs) else WebFetchArgs.model_validate(args)
    url = typed.url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("web_fetch 只允许 http/https URL")
    timeout_seconds = int(workspace.config.get("search", {}).get("timeout_seconds", 20) or 20)
    data, content_type, final_url = fetch_url_bytes(url, timeout_seconds=timeout_seconds)
    text = data.decode("utf-8", errors="replace")
    if "html" in content_type:
        text = strip_html_tags(text)
    text = text.strip()
    truncated = len(text) > typed.max_chars
    return {
        "url": url,
        "final_url": final_url,
        "content_type": content_type,
        "content": text[:typed.max_chars],
        "truncated": truncated,
        "summary": f"抓取 {final_url}，返回 {min(len(text), typed.max_chars)} 字符" + ("（内容已截断）" if truncated else ""),
    }


def default_search_engines(query: str) -> list[str]:
    if re.search(r"[\u4e00-\u9fff]", query):
        return ["sogou", "duckduckgo", "bing_cn"]
    return ["duckduckgo", "bing"]


def handle_web_search(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, WebSearchArgs) else WebSearchArgs.model_validate(args)
    timeout_seconds = int(workspace.config.get("search", {}).get("timeout_seconds", 20) or 20)
    engines = typed.engines or default_search_engines(typed.query)
    results = web_search_results(
        typed.query,
        engines,
        max_results=typed.max_results,
        timeout_seconds=timeout_seconds,
    )
    useful_count = len([item for item in results if item.get("title") != "搜索失败"])
    return {
        "query": typed.query,
        "engines": engines,
        "count": useful_count,
        "results": results,
        "supported_engine_names": ["baidu", "Baidu", "bing_cn", "Bing CN", "bing", "duckduckgo", "google", "sogou", "360"],
        "summary": f"搜索 {typed.query}，返回 {useful_count} 条结果",
    }


def handle_web_read(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, WebReadArgs) else WebReadArgs.model_validate(args)
    url = typed.url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("web_read 只允许 http/https URL")
    timeout_seconds = int(workspace.config.get("search", {}).get("timeout_seconds", 20) or 20)
    data, content_type, final_url = fetch_url_bytes(url, timeout_seconds=timeout_seconds)
    text = data.decode("utf-8", errors="replace")
    if "html" in content_type:
        text = strip_html_tags(text)
    text = text.strip()
    truncated = len(text) > typed.max_chars
    return {
        "url": url,
        "final_url": final_url,
        "content_type": content_type,
        "content": text[:typed.max_chars],
        "truncated": truncated,
        "summary": f"读取 {final_url}，返回 {min(len(text), typed.max_chars)} 字符" + ("（内容已截断）" if truncated else ""),
    }


def build_agent_tool_registry(config: dict[str, Any]) -> dict[str, AgentToolSpec]:
    tools = [
        AgentToolSpec(
            name="get_current_holdings",
            description="读取当前脱敏持仓，只返回白名单字段。",
            args_model=GetCurrentHoldingsArgs,
            handler=handle_get_current_holdings,
        ),
        AgentToolSpec(
            name="get_portfolio_profile",
            description="生成组合画像、资产大类/行业/主题暴露和组合观察项。",
            args_model=GetPortfolioProfileArgs,
            handler=handle_get_portfolio_profile,
        ),
        AgentToolSpec(
            name="get_holding_technical",
            description="读取指定当前持仓标的的技术指标和规则信号。",
            args_model=GetHoldingTechnicalArgs,
            handler=handle_get_holding_technical,
        ),
        AgentToolSpec(
            name="get_classification",
            description="读取指定当前持仓标的的本地分类和置信度，不触发外部搜索。",
            args_model=GetClassificationArgs,
            handler=handle_get_classification,
        ),
        AgentToolSpec(
            name="load_snapshot_summary",
            description="读取最新历史快照摘要，不返回原始账户响应。",
            args_model=LoadSnapshotSummaryArgs,
            handler=handle_load_snapshot_summary,
        ),
        AgentToolSpec(
            name="compare_snapshots",
            description="对比当前 workspace 事实数据和最新历史快照。",
            args_model=CompareSnapshotsArgs,
            handler=handle_compare_snapshots,
        ),
        AgentToolSpec(
            name="generate_candidate_actions",
            description="基于已确认 policy 生成候选动作。第一版未启用时会返回不可用。",
            args_model=GenerateCandidateActionsArgs,
            handler=handle_generate_candidate_actions,
        ),
    ]
    if config_bool(config.get("skills", {}).get("enabled", True)):
        tools.extend([
            AgentToolSpec(
                name="list_skills",
                description=(
                    "列出已安装的本地 skills。任务可能需要专门方法、流程或用户自定义能力时，"
                    "先调用它发现可用 skill。"
                ),
                args_model=ListSkillsArgs,
                handler=handle_list_skills,
                permission="skills:read",
            ),
            AgentToolSpec(
                name="read_skill",
                description=(
                    "读取指定 skill 的 SKILL.md 内容，用于按用户安装的 skill 工作。"
                    "只能读取已安装 skill，不会联网或执行 skill 中的命令。"
                ),
                args_model=ReadSkillArgs,
                handler=handle_read_skill,
                permission="skills:read",
            ),
            AgentToolSpec(
                name="list_skill_files",
                description="列出指定 skill 包内的可读配套文件，例如 references、config、metadata。",
                args_model=ListSkillFilesArgs,
                handler=handle_list_skill_files,
                permission="skills:read",
            ),
            AgentToolSpec(
                name="read_skill_file",
                description="读取指定 skill 包内的配套文本文件。只能读取 skill 目录内的相对路径。",
                args_model=ReadSkillFileArgs,
                handler=handle_read_skill_file,
                permission="skills:read",
            ),
        ])
    if config_bool(config.get("agent", {}).get("allow_external_search_tools", False)):
        tools.extend([
            AgentToolSpec(
                name="web_search",
                description=(
                    "用一个或多个搜索引擎执行网页搜索，返回结构化结果 title/url/snippet。"
                    "优先用它搜索，再用 web_read 打开具体结果页。"
                ),
                args_model=WebSearchArgs,
                handler=handle_web_search,
                permission="web:read",
            ),
            AgentToolSpec(
                name="web_read",
                description="读取指定网页并提取正文文本，适合打开 web_search 返回的具体结果页。",
                args_model=WebReadArgs,
                handler=handle_web_read,
                permission="web:read",
            ),
            AgentToolSpec(
                name="web_fetch",
                description=(
                    "底层按 URL 抓取网页文本。优先使用 web_search/web_read；"
                    "仅在需要直接访问特定 URL 时使用。"
                ),
                args_model=WebFetchArgs,
                handler=handle_web_fetch,
                permission="web:read",
            ),
        ])
    return {tool.name: tool for tool in tools}


def tool_schema(tool: AgentToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.args_model.model_json_schema(),
        },
    }


def tool_schemas(registry: dict[str, AgentToolSpec]) -> list[dict[str, Any]]:
    return [tool_schema(tool) for tool in registry.values()]
