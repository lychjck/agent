import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    engines: list[str] = Field(
        default_factory=list, 
        max_length=8,
        description="兼容旧参数。当前 web_search 统一使用 opencli，忽略 engines。"
    )
    max_results: int = Field(default=8, ge=1, le=10)


class WebReadArgs(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    max_chars: int = Field(default=12000, ge=1000, le=30000)


class OpenCliCommandArgs(BaseModel):
    site: str = Field(
        min_length=1,
        max_length=80,
        description="opencli site adapter 名称，例如 duckduckgo、google、eastmoney、sinafinance、xueqiu、web。",
    )
    command: str = Field(
        min_length=1,
        max_length=80,
        description="opencli 命令名称，例如 search、quote、etf、kline、news、read。",
    )
    positionals: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="位置参数，按 opencli <site> <command> 的 usage 顺序填写，例如搜索词、股票代码、URL。",
    )
    options: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="命令选项，不带 -- 前缀，例如 limit=10、region=cn-zh、type=1、url=https://...。",
    )


STOCK_CODE_PREFIXES = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688")
FUND_CODE_PREFIXES = ("007", "010", "011", "012", "013", "014", "015", "016", "017", "018", "019", "020", "021")
ETF_CODE_PREFIXES = ("15", "16", "50", "51", "52", "56", "58")
FUND_NAME_TOKENS = ("ETF", "LOF", "基金", "联接", "债券", "混合", "指数", "增强", "货币")
NON_ETF_FUND_NAME_TOKENS = ("联接", "债券", "混合", "增强", "货币")


def is_stock_like_holding(code: str, name: str, asset_type: str = "") -> bool:
    normalized_code = str(code or "").strip()
    normalized_name = str(name or "").strip().upper()
    normalized_type = str(asset_type or "").strip().lower()
    if normalized_type == "stock":
        return True
    if any(token in normalized_name for token in FUND_NAME_TOKENS):
        return False
    if normalized_code.startswith(FUND_CODE_PREFIXES):
        return False
    return len(normalized_code) == 6 and normalized_code.startswith(STOCK_CODE_PREFIXES)


def is_etf_like_holding(code: str, name: str, asset_type: str = "") -> bool:
    normalized_code = str(code or "").strip()
    normalized_name = str(name or "").strip().upper()
    normalized_type = str(asset_type or "").strip().lower()
    if normalized_type != "etf":
        return False
    if is_stock_like_holding(normalized_code, normalized_name, normalized_type):
        return False
    if normalized_code.startswith(FUND_CODE_PREFIXES):
        return False
    if any(token in normalized_name for token in NON_ETF_FUND_NAME_TOKENS):
        return False
    return (
        "ETF" in normalized_name
        or "LOF" in normalized_name
        or normalized_code.startswith(ETF_CODE_PREFIXES)
    )


def holding_query_suffix(query: str) -> str:
    text = str(query or "")
    parts: list[str] = []
    if "近期" in text:
        parts.append("近期")
    if any(token in text for token in ("表现", "行情", "走势")):
        parts.append("表现")
    if "驱动" in text:
        parts.append("驱动因素")
    if any(token in text for token in ("风险", "风险点")):
        parts.append("风险点")
    if any(token in text for token in ("新闻", "动态", "重大")):
        parts.append("新闻动态")
    if not parts:
        parts = ["近期表现", "驱动因素", "风险点"]
    output: list[str] = []
    for part in parts:
        if part not in output:
            output.append(part)
    return " ".join(output)


def split_multi_holding_query(query: str, workspace: AgentWorkspace) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return []
    matches: list[tuple[str, str]] = []
    for holding in workspace.ensure_holdings():
        code = str(holding.code or "").strip()
        name = str(holding.name or "").strip()
        if not code:
            continue
        if code in text or (name and name in text):
            matches.append((code, name))
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for code, name in matches:
        if code in seen:
            continue
        seen.add(code)
        unique.append((code, name))
    if len(unique) <= 1:
        return [text]
    suffix = holding_query_suffix(text)
    return [f"{code} {name} {suffix}".strip() for code, name in unique]


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


def handle_web_search(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, WebSearchArgs) else WebSearchArgs.model_validate(args)
    search_config = workspace.config.get("search", {})
    opencli_search_config = dict(workspace.config)
    opencli_search = dict(search_config) if isinstance(search_config, dict) else {}
    opencli_search["enabled"] = True
    opencli_search["provider"] = "opencli"
    opencli_search_config["search"] = opencli_search

    from stock_assistant.integrations.search import build_search_provider

    provider = build_search_provider(opencli_search_config)
    queries = split_multi_holding_query(typed.query, workspace)
    max_workers = min(6, max(1, len(queries)))
    results_by_query: list[dict[str, Any]] = []
    if len(queries) == 1:
        results_by_query.append({"query": queries[0], "results": provider.search(queries[0], typed.max_results)})
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(provider.search, query, typed.max_results): query
                for query in queries
            }
            for future in as_completed(futures):
                query = futures[future]
                try:
                    results_by_query.append({"query": query, "results": future.result()})
                except Exception as exc:  # noqa: BLE001
                    results_by_query.append({"query": query, "results": [], "error": str(exc)})
        results_by_query.sort(key=lambda item: queries.index(str(item.get("query", ""))))
    results = [
        item
        for group in results_by_query
        for item in list(group.get("results") or [])
    ]
    return {
        "query": typed.query,
        "queries": queries,
        "split": len(queries) > 1,
        "engines": ["opencli"],
        "count": len(results),
        "results_by_query": [
            {
                "query": str(group.get("query", "")),
                "count": len(group.get("results") or []),
                "error": str(group.get("error", "")),
            }
            for group in results_by_query
        ],
        "results": [
            {
                "engine": str(item.get("source", "opencli")),
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("snippet", "")),
            }
            for item in results
        ],
        "summary": (
            f"opencli 搜索 {typed.query}，拆分为 {len(queries)} 个查询并返回 {len(results)} 条结果"
            if len(queries) > 1
            else f"opencli 搜索 {typed.query}，返回 {len(results)} 条结果"
        ),
    }


def content_quality_warning(text: str, url: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    login_hits = sum(1 for token in ("登录", "注册", "验证码", "请登录", "扫码", "立即登录") if token in normalized)
    finance_hits = sum(1 for token in ("行情", "市场", "基金", "ETF", "股票", "指数", "债券", "收益", "风险") if token in normalized)
    if login_hits >= 2 and finance_hits <= 1:
        return "页面疑似登录/拦截页，正文质量低"
    if "xueqiu.com" in url and login_hits >= 2 and finance_hits <= 2:
        return "雪球页面疑似登录/拦截页，正文质量低"
    if len(normalized) < 120:
        return "正文过短，可能不是有效来源页"
    return ""


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
    quality_warning = content_quality_warning(text, final_url)
    return {
        "url": url,
        "final_url": final_url,
        "content_type": content_type,
        "content": text[:typed.max_chars],
        "truncated": truncated,
        "content_quality": "low" if quality_warning else "normal",
        "quality_warning": quality_warning,
        "summary": (
            f"读取 {final_url}，返回 {min(len(text), typed.max_chars)} 字符"
            + ("（内容已截断）" if truncated else "")
            + (f"；警告：{quality_warning}" if quality_warning else "")
        ),
    }


DEFAULT_OPENCLI_COMMANDS = {
    "duckduckgo": {"search", "suggest"},
    "google": {"search", "news", "suggest", "trends"},
    "brave": {"search"},
    "yahoo": {"search"},
    "eastmoney": {
        "announcement",
        "convertible",
        "etf",
        "holders",
        "index-board",
        "kline",
        "kuaixun",
        "longhu",
        "money-flow",
        "northbound",
        "quote",
        "rank",
        "sectors",
    },
    "sinafinance": {"news", "stock"},
    "xueqiu": {"search", "stock", "kline", "hot-stock"},
    "yahoo-finance": {"quote"},
    "barchart": {"quote", "options", "greeks"},
    "bloomberg": {"main", "markets", "economics", "industries", "tech", "politics"},
    "reuters": {"search", "article-detail"},
    "web": {"read"},
}


def allowed_opencli_commands(config: dict[str, Any]) -> dict[str, set[str]]:
    configured = config.get("search", {}).get("providers", {}).get("opencli", {}).get("allowed_commands", {})
    if not isinstance(configured, dict):
        return DEFAULT_OPENCLI_COMMANDS
    allowed: dict[str, set[str]] = {}
    for site, commands in configured.items():
        if isinstance(commands, str):
            allowed[str(site)] = {item.strip() for item in commands.split(",") if item.strip()}
        elif isinstance(commands, list):
            allowed[str(site)] = {str(item).strip() for item in commands if str(item).strip()}
    return allowed or DEFAULT_OPENCLI_COMMANDS


def validate_opencli_option_name(name: str) -> str:
    normalized = name.strip().replace("_", "-")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,60}", normalized):
        raise ValueError(f"不安全的 opencli option: {name}")
    return normalized


def handle_opencli_command(args: BaseModel, workspace: AgentWorkspace) -> dict[str, Any]:
    typed = args if isinstance(args, OpenCliCommandArgs) else OpenCliCommandArgs.model_validate(args)
    site = typed.site.strip()
    command = typed.command.strip()
    allowed = allowed_opencli_commands(workspace.config)
    if command not in allowed.get(site, set()):
        raise ValueError(f"opencli 命令不在允许列表中: {site} {command}")

    search_config = workspace.config.get("search", {})
    opencli_config = search_config.get("providers", {}).get("opencli", {}) if isinstance(search_config, dict) else {}
    command_path = str(opencli_config.get("command_path", "opencli"))
    if "/" not in command_path and shutil.which(command_path) is None:
        raise RuntimeError(f"opencli command not found: {command_path}")

    cmd = [command_path]
    profile = str(opencli_config.get("profile", "")).strip()
    if profile:
        cmd.extend(["--profile", profile])
    cmd.extend([site, command])
    cmd.extend(str(item) for item in typed.positionals)
    for key, value in typed.options.items():
        option = validate_opencli_option_name(str(key))
        if isinstance(value, bool):
            if value:
                cmd.append(f"--{option}")
            continue
        cmd.extend([f"--{option}", str(value)])
    cmd.extend(["-f", "json"])

    timeout_seconds = int(search_config.get("timeout_seconds", 20) or 20)
    def run_command(command_args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command_args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

    try:
        completed = run_command(cmd)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"opencli 执行失败: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        fallback_completed: subprocess.CompletedProcess[str] | None = None
        symbol = str((typed.positionals or [""])[0]).strip().upper()
        if site == "eastmoney" and command == "quote" and symbol in {"HSI", "HSTECH", "NDX", "IXIC", "DJI", "SPX"}:
            fallback_cmd = [command_path]
            if profile:
                fallback_cmd.extend(["--profile", profile])
            fallback_cmd.extend(["xueqiu", "stock", symbol, "-f", "json"])
            try:
                fallback_completed = run_command(fallback_cmd)
            except Exception:
                fallback_completed = None
        if fallback_completed is None or fallback_completed.returncode != 0:
            raise RuntimeError(f"opencli 执行失败 exit={completed.returncode}: {detail}")
        completed = fallback_completed
        site = "xueqiu"
        command = "stock"
    try:
        payload: Any = json.loads(completed.stdout)
    except Exception:
        payload = completed.stdout.strip()
    count = len(payload) if isinstance(payload, list) else 1 if payload else 0
    return {
        "site": site,
        "command": command,
        "count": count,
        "result": payload,
        "summary": f"opencli {site} {command} 返回 {count} 条/组结果",
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
            description="读取指定当前持仓标的的技术指标和事实观察。",
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
                name="opencli_command",
                description=(
                    "调用 opencli 的只读站点适配器命令，返回 JSON。先按任务选择具体 site/command，"
                    "例如 duckduckgo/google/brave/yahoo search 做通用搜索；eastmoney quote/etf/kline/sectors/kuaixun/rank "
                    "抓 A 股/ETF/板块/快讯；sinafinance news/stock 抓新浪财经快讯和行情；"
                    "xueqiu search/stock/kline/hot-stock 抓雪球股票信息；web read 用浏览器把 URL 导出为 Markdown。"
                    "不知道某个命令参数时，按 opencli list/help 暴露的 usage 组织 positionals/options。"
                ),
                args_model=OpenCliCommandArgs,
                handler=handle_opencli_command,
                permission="web:read",
            ),
            AgentToolSpec(
                name="web_search",
                description=(
                    "通过 opencli 执行网页搜索，返回结构化结果 title/url/snippet。"
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
