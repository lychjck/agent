from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stock_assistant.agents.agent_llm import classification_record, technical_record
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.models import holding_to_dict


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


def build_agent_tool_registry(config: dict[str, Any]) -> dict[str, AgentToolSpec]:
    _ = config
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
