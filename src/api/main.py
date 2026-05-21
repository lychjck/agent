from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
import asyncio
import datetime as dt
import json
import httpx
import threading
import uuid

from stock_assistant import (
    load_config, load_env_file, ensure_dirs, fetch_tzzb_holdings,
    llm_enabled, log, holding_to_dict, load_latest_agent_snapshot, list_agent_snapshots,
    save_agent_snapshot,
)
from stock_assistant.agents.agent import run_agent_analysis, run_agent_analysis_events
from stock_assistant.agents.agent_loop import run_tool_agent_events
from stock_assistant.core.llm_tools import parse_llm_tool_step
from stock_assistant.core.llm import get_modelscope_rate_limit
from stock_assistant.cli.cli import build_portfolio_profile
from stock_assistant.core.utils import config_bool
import logging
import sys


class AgentRunPollingAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "GET /api/agent/run/" not in message


def setup_basic_logging(level: int = logging.INFO) -> None:
    """初始化基础日志配置"""
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    
    root = logging.getLogger()
    if root.handlers:
        for h in root.handlers:
            root.removeHandler(h)
            
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("uvicorn.access").addFilter(AgentRunPollingAccessFilter())

# 初始化日志系统
setup_basic_logging()

app = FastAPI(title="投资账本 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CONFIG_PATH = Path("config.toml")
load_env_file(Path(".env"))
config = load_config(CONFIG_PATH)
ensure_dirs(config)

class AnalyzeRequest(BaseModel):
    cached_results: list[dict] | None = None
    model: str | None = None

class AgentRunRequest(BaseModel):
    cached_results: list[dict] | None = None
    model: str | None = None
    mode: str = "pipeline"
    goal: str | None = None
    resume_state: dict | None = None


class AgentRunResumeRequest(BaseModel):
    model: str | None = None


agent_runs: dict[str, dict] = {}
agent_runs_lock = threading.Lock()


def agent_run_checkpoint_dir() -> Path:
    path = Path(config.get("agent", {}).get("snapshot_dir", "data/state")).expanduser() / "agent_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_run_checkpoint_path(run_id: str) -> Path:
    safe_id = "".join(ch for ch in run_id if ch.isalnum() or ch in {"-", "_"})
    return agent_run_checkpoint_dir() / f"{safe_id}.json"


def save_agent_run_checkpoint(run_id: str) -> None:
    with agent_runs_lock:
        record = agent_runs.get(run_id)
        if not record or record.get("status") != "paused":
            return
        payload = {
            key: value
            for key, value in record.items()
            if key not in {"thread"}
        }
    path = agent_run_checkpoint_path(run_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已保存 Agent 暂停状态: {path}", name="api")


def write_agent_run_checkpoint_payload(run_id: str, payload: dict) -> None:
    path = agent_run_checkpoint_path(run_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已更新 Agent 暂停状态: {path}", name="api")


def load_agent_run_checkpoint(run_id: str) -> dict | None:
    path = agent_run_checkpoint_path(run_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"读取 Agent 暂停状态失败 path={path}: {exc}", level="WARN", name="api")
        return None
    if not isinstance(payload, dict) or payload.get("run_id") != run_id:
        return None
    payload["thread"] = None
    return payload


def delete_agent_run_checkpoint(run_id: str) -> None:
    path = agent_run_checkpoint_path(run_id)
    if path.exists():
        path.unlink()


def get_or_load_agent_run(run_id: str) -> dict | None:
    with agent_runs_lock:
        record = agent_runs.get(run_id)
    if record is not None:
        return record
    record = load_agent_run_checkpoint(run_id)
    if record is None:
        return None
    with agent_runs_lock:
        agent_runs[run_id] = record
    return record


def max_agent_run_events() -> int:
    value = config.get("agent", {}).get("max_run_events", 2000)
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = 2000
    return max(1, limit)


def append_agent_run_event(run_id: str, event: dict) -> None:
    with agent_runs_lock:
        record = agent_runs.get(run_id)
        if record is None:
            return
        events = record.setdefault("events", [])
        event_payload = dict(event)
        event_payload.setdefault("run_id", run_id)
        event_payload["event_index"] = len(events)
        events.append(event_payload)
        limit = max_agent_run_events()
        if len(events) > limit:
            del events[: len(events) - limit]
            for index, item in enumerate(events):
                item["event_index"] = index
        record["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")


def update_agent_run(run_id: str, **updates: object) -> None:
    with agent_runs_lock:
        record = agent_runs.get(run_id)
        if record is None:
            return
        record.update(updates)
        record["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")


def agent_run_cancel_requested(run_id: str) -> bool:
    with agent_runs_lock:
        record = agent_runs.get(run_id)
        return bool(record and record.get("cancel_requested"))


def mark_agent_run_cancelled(run_id: str, message: str = "用户已终止 AI 分析") -> None:
    update_agent_run(
        run_id,
        status="cancelled",
        cancel_requested=True,
        error=message,
    )


def latest_agent_trace_path() -> Path | None:
    trace_dir = Path(config.get("agent", {}).get("trace_dir", "data/state/agent_traces")).expanduser()
    if not trace_dir.exists():
        return None
    traces = [path for path in trace_dir.glob("agent-*.jsonl") if path.is_file()]
    if not traces:
        return None
    return max(traces, key=lambda path: path.stat().st_mtime)


def recover_snapshot_from_trace(path: Path) -> dict | None:
    model = "unknown"
    final_report: dict | None = None
    final_turn = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("type") == "agent_start" and record.get("model"):
                model = str(record.get("model"))
            if record.get("type") != "llm_response":
                continue
            raw_text = str(record.get("raw_text") or "")
            if not raw_text:
                continue
            try:
                step = parse_llm_tool_step(raw_text)
            except Exception:
                continue
            if step.type == "final_report" and isinstance(step.final_report, dict):
                final_report = step.final_report
                final_turn = record.get("turn")
    except Exception as exc:  # noqa: BLE001
        log(f"从 trace 恢复报告失败 path={path}: {exc}", level="WARN", name="api")
        return None
    if not final_report:
        return None
    now = dt.datetime.now().isoformat(timespec="seconds")
    snapshot = {
        "schema_version": 1,
        "generated_at": now,
        "source": f"recovered_trace:{path.name}",
        "ledger_summary": {},
        "portfolio": {},
        "classifications": {},
        "technical_results": [],
        "observations": [],
        "risk_flags": [],
        "candidate_actions": [],
        "agent_report": final_report,
        "model": model,
        "recovered_from_trace": {
            "path": str(path),
            "turn": final_turn,
            "recovered_at": now,
        },
    }
    save_agent_snapshot(snapshot, config)
    log(f"已从 trace 恢复 final_report: {path}", name="api")
    return snapshot


def recover_latest_trace_snapshot_if_needed() -> dict | None:
    trace_path = latest_agent_trace_path()
    if trace_path is None:
        return None
    snapshots = list_agent_snapshots(config)
    if snapshots and snapshots[-1].stat().st_mtime >= trace_path.stat().st_mtime:
        return None
    return recover_snapshot_from_trace(trace_path)


async def run_agent_job(run_id: str, req: AgentRunRequest) -> None:
    update_agent_run(run_id, status="running")
    try:
        if req.mode == "tool_agent" or (
            req.mode == "default" and config_bool(config.get("agent", {}).get("tool_agent_default", False))
        ):
            goal = req.goal or "分析当前持仓，给出组合风险、每个 ETF 的建议和需要确认的问题"
            event_iter = run_tool_agent_events(
                config,
                goal=goal,
                cached_results=req.cached_results,
                model_override=req.model,
                resume_state=req.resume_state,
            )
        else:
            event_iter = run_agent_analysis_events(
                config,
                cached_results=req.cached_results,
                model_override=req.model,
            )

        async for event in event_iter:
            if agent_run_cancel_requested(run_id):
                mark_agent_run_cancelled(run_id)
                return
            append_agent_run_event(run_id, event)
            if agent_run_cancel_requested(run_id):
                mark_agent_run_cancelled(run_id)
                return
            if event.get("step") == "error":
                update_agent_run(
                    run_id,
                    status="error",
                    error=str(event.get("error") or event.get("status") or "agent failed"),
                )
            if event.get("step") == "paused":
                update_agent_run(
                    run_id,
                    status="paused",
                    error=str(event.get("error") or event.get("status") or "agent paused"),
                    checkpoint=event.get("checkpoint"),
                    request=req.model_dump(),
                )
                save_agent_run_checkpoint(run_id)
            if event.get("step") == "done":
                snapshot = event.get("snapshot")
                if isinstance(snapshot, dict):
                    update_agent_run(run_id, snapshot=snapshot)
                update_agent_run(run_id, status="completed")
                delete_agent_run_checkpoint(run_id)
        with agent_runs_lock:
            status = agent_runs.get(run_id, {}).get("status")
        if status == "running":
            update_agent_run(run_id, status="completed")
            delete_agent_run_checkpoint(run_id)
    except asyncio.CancelledError:
        mark_agent_run_cancelled(run_id, "agent run cancelled")
        append_agent_run_event(run_id, {"step": "error", "status": "执行已取消", "error": "agent run cancelled"})
        raise
    except Exception as e:  # noqa: BLE001
        update_agent_run(run_id, status="error", error=str(e))
        log(f"agent job 失败 run_id={run_id}: {e}", level="ERROR", name="api")
        append_agent_run_event(run_id, {"step": "error", "status": "Agent 后台执行失败", "error": str(e)})


def run_agent_job_in_thread(run_id: str, req: AgentRunRequest) -> None:
    asyncio.run(run_agent_job(run_id, req))


def infer_model_provider(base_url: str) -> str:
    normalized = base_url.lower()
    if "easyrouter" in normalized:
        return "EasyRouter"
    if "modelscope" in normalized:
        return "ModelScope"
    if "localhost" in normalized or "127.0.0.1" in normalized or "10." in normalized:
        return "Local"
    return "OpenAI Compatible"


def model_display_name(model_id: str) -> str:
    aliases = {
        "deepseek-v4-pro": "DeepSeek V4 Pro",
        "google/gemma-4-26b-a4b": "Gemma 4 26B A4B",
        "inclusionAI/Ling-2.6-1T": "Ling-2.6-1T",
        "ZhipuAI/GLM-5.1": "GLM-5.1",
        "moonshotai/Kimi-K2.5": "Kimi-K2.5",
        "deepseek-ai/DeepSeek-V3": "DeepSeek V3",
        "deepseek-ai/DeepSeek-V4-Pro": "DeepSeek V4 Pro",
    }
    if model_id in aliases:
        return aliases[model_id]
    return model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id


def modelscope_usage_note() -> str:
    return (
        "ModelScope API-Inference 免费额度：用户总额度当前每天 2000 次；"
        "单模型每日额度动态调整，最高不超过 200，实际可能更低；"
        "并发和速率限制会随平台压力动态调整，原则上保障单并发使用。"
    )


def model_rate_limit_info(provider: str, resolved_model: str) -> dict | None:
    if provider != "ModelScope":
        return None
    info = get_modelscope_rate_limit(resolved_model)
    if not info:
        return {"provider": "ModelScope", "status": "unknown", "note": "尚未看到该模型的 API-Inference 响应头，运行一次后会显示剩余额度。"}
    return {**info, "status": "known", "note": modelscope_usage_note()}


def configured_llm_models() -> list[dict]:
    llm_config = config.get("llm", {})
    default_model = str(llm_config.get("model", "")).strip()
    models: list[dict] = []
    if default_model:
        provider = infer_model_provider(str(llm_config.get("base_url", "")))
        models.append({
            "id": default_model,
            "name": model_display_name(default_model),
            "provider": provider,
            "default": True,
            "rate_limit": model_rate_limit_info(provider, default_model),
        })

    profiles = llm_config.get("model_profiles", {})
    if isinstance(profiles, dict):
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            model_id = str(profile_id).strip()
            if not model_id:
                continue
            resolved_model = str(profile.get("model") or model_id).strip()
            provider = infer_model_provider(str(profile.get("base_url", llm_config.get("base_url", ""))))
            models.append({
                "id": model_id,
                "name": model_display_name(resolved_model),
                "provider": provider,
                "default": model_id == default_model,
                "rate_limit": model_rate_limit_info(provider, resolved_model),
            })

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in models:
        model_id = str(item.get("id", "")).strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        deduped.append(item)
    return deduped


@app.get("/api/agent/models")
def get_agent_models():
    return {
        "models": configured_llm_models(),
        "default_model": str(config.get("llm", {}).get("model", "")).strip(),
    }


@app.get("/api/holdings")
def get_holdings():
    log("GET /api/holdings - 开始获取持仓数据", name="api")
    try:
        if str(config.get("ledger", {}).get("mode", "")).strip().lower() == "tzzb_api":
            holdings, archived, summary = fetch_tzzb_holdings(config)
            log(f"成功获取 {len(holdings)} 条持仓记录", name="api")
            
            serializable_results = []
            for h in holdings:
                res = holding_to_dict(h)
                res["weight"] = (h.market_value / sum(item.market_value or 0 for item in holdings) * 100) if h.market_value and any(item.market_value for item in holdings) else None
                serializable_results.append(res)
            
            total_value = summary.get("total_asset") or sum(item.market_value or 0 for item in holdings)
            total_profit = summary.get("total_profit") or sum((item.market_value or 0) - (item.cost_price or 0) * (item.quantity or 0) for item in holdings if item.cost_price and item.quantity)
            day_profit = summary.get("day_profit")
            
            return {
                "total_value": total_value,
                "total_profit": total_profit,
                "day_profit": day_profit,
                "holdings": serializable_results
            }
        else:
            log("获取持仓失败: 模式不支持", level="ERROR", name="api")
            return {"error": "当前仅支持 tzzb_api 模式作为后端源。请在 config.toml 设置 [ledger] mode='tzzb_api'"}
    except Exception as e:
        log(f"GET /api/holdings 发生异常: {str(e)}", level="ERROR", name="api")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/profile")
def get_profile(refresh_classification: bool = False):
    log(f"GET /api/profile - 开始生成组合画像 (refresh={refresh_classification})", name="api")
    try:
        profile = build_portfolio_profile(
            config,
            refresh_classification=refresh_classification,
        )
        log("组合画像生成成功", name="api")
        return profile
    except Exception as e:
        log(f"GET /api/profile 发生异常: {str(e)}", level="ERROR", name="api")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/classify/{code}")
def classify_one(code: str):
    log(f"POST /api/classify/{code} - 强制触发单个标的分类", name="api")
    try:
        from stock_assistant.cli.cli import load_profile_holdings
        from stock_assistant.integrations.search import suggest_classification_with_search
        
        holdings, _, _ = load_profile_holdings(config)
        holding = next((h for h in holdings if h.code == code), None)
        
        if not holding:
            log(f"分类失败: 未找到持仓 {code}", level="WARN", name="api")
            raise HTTPException(status_code=404, detail=f"未找到代码为 {code} 的持仓")
            
        cls = suggest_classification_with_search(holding, config)
        if not cls:
            log(f"分类失败: AI 搜索未返回结果 {code}", level="ERROR", name="api")
            raise HTTPException(status_code=500, detail="AI 分类搜索未能返回结果")
            
        log(f"成功完成分类: {code} -> {cls.asset_class}", name="api")
        return {
            "code": cls.code,
            "name": cls.name,
            "asset_class": cls.asset_class,
            "sector": cls.sector,
            "theme": cls.theme,
            "region": cls.region,
            "strategy": cls.strategy,
            "tracked_index": cls.tracked_index,
            "issuer": cls.issuer,
            "confidence": cls.confidence,
            "source": cls.source,
            "reviewed_by_user": cls.reviewed_by_user,
        }
    except HTTPException:
        raise
    except Exception as e:
        log(f"POST /api/classify/{code} 发生异常: {str(e)}", level="ERROR", name="api")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/klines")
async def get_klines(symbol: str, date: str = ""):
    base_url = "https://yinglian.site/api/klines"
    params = {"symbol": symbol}
    if date:
        params["date"] = date
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(base_url, params=params, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"获取 K 线失败: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch klines: {str(e)}")

@app.get("/api/klines/daily")
async def get_daily_klines(symbol: str):
    base_url = "https://yinglian.site/api/klines/daily"
    params = {"symbol": symbol}
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(base_url, params=params, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"获取日线失败: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch daily klines: {str(e)}")

def sse_payload(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def legacy_analyze_step(event: dict) -> int:
    step = event.get("step")
    if step in {"sync_holdings", "market_data"}:
        return 1
    if step == "technical_analysis":
        return 2
    if step in {"classify", "portfolio_profile", "portfolio_observations", "llm_report"}:
        return 3
    if step in {"save_snapshot", "done"}:
        return 4
    return 0


@app.post("/api/analyze")
async def analyze_legacy(req: AnalyzeRequest = AnalyzeRequest()):
    async def event_generator():
        try:
            async for event in run_agent_analysis_events(
                config,
                cached_results=req.cached_results,
                model_override=req.model,
            ):
                payload = dict(event)
                payload["step"] = legacy_analyze_step(event)
                if event.get("step") == "done":
                    snapshot = event.get("snapshot")
                    if isinstance(snapshot, dict):
                        payload["result"] = snapshot.get("agent_report")
                yield sse_payload(payload)
        except Exception as e:  # noqa: BLE001
            log(f"legacy analyze 失败: {e}", level="ERROR", name="api")
            yield sse_payload({"step": 0, "error": str(e)})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/agent/run/stream")
async def run_agent_stream(req: AgentRunRequest = AgentRunRequest()):
    async def event_generator():
        try:
            if req.mode == "tool_agent" or (
                req.mode == "default" and config_bool(config.get("agent", {}).get("tool_agent_default", False))
            ):
                goal = req.goal or "分析当前持仓，给出组合风险、每个 ETF 的建议和需要确认的问题"
                async for event in run_tool_agent_events(
                    config,
                    goal=goal,
                    cached_results=req.cached_results,
                    model_override=req.model,
                ):
                    yield sse_payload(event)
            else:
                async for event in run_agent_analysis_events(
                    config,
                    cached_results=req.cached_results,
                    model_override=req.model,
                ):
                    yield sse_payload(event)
        except Exception as e:  # noqa: BLE001
            log(f"agent stream 失败: {e}", level="ERROR", name="api")
            yield sse_payload({"step": "error", "error": str(e)})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/agent/run/start")
async def start_agent_run(req: AgentRunRequest = AgentRunRequest()):
    run_id = f"agent-ui-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    now = dt.datetime.now().isoformat(timespec="seconds")
    with agent_runs_lock:
        agent_runs[run_id] = {
            "run_id": run_id,
            "status": "queued",
            "events": [],
            "snapshot": None,
            "error": "",
            "started_at": now,
            "updated_at": now,
            "thread": None,
        }
    thread = threading.Thread(target=run_agent_job_in_thread, args=(run_id, req), daemon=True)
    with agent_runs_lock:
        agent_runs[run_id]["thread"] = thread
    thread.start()
    return {"run_id": run_id, "status": "queued"}


@app.post("/api/agent/run/{run_id}/resume")
async def resume_agent_run(run_id: str, resume_req: AgentRunResumeRequest = AgentRunResumeRequest()):
    record = get_or_load_agent_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="agent run not found")
    checkpoint_payload = None
    with agent_runs_lock:
        record = agent_runs[run_id]
        if record.get("status") != "paused":
            return {"run_id": run_id, "status": record.get("status")}
        checkpoint = record.get("checkpoint")
        request_payload = dict(record.get("request") or {})
        if not isinstance(checkpoint, dict):
            raise HTTPException(status_code=409, detail="agent run has no checkpoint")
        requested_model = str(resume_req.model or "").strip()
        if requested_model:
            request_payload["model"] = requested_model
        request_payload["resume_state"] = checkpoint
        req = AgentRunRequest.model_validate(request_payload)
        record["request"] = req.model_dump()
        checkpoint_payload = {
            key: value
            for key, value in record.items()
            if key not in {"thread"}
        }
        record["status"] = "queued"
        record["error"] = ""
        record["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    if checkpoint_payload is not None:
        write_agent_run_checkpoint_payload(run_id, checkpoint_payload)
    thread = threading.Thread(target=run_agent_job_in_thread, args=(run_id, req), daemon=True)
    with agent_runs_lock:
        agent_runs[run_id]["thread"] = thread
    thread.start()
    return {"run_id": run_id, "status": "queued"}


@app.post("/api/agent/run/{run_id}/cancel")
async def cancel_agent_run(run_id: str):
    record = get_or_load_agent_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="agent run not found")
    with agent_runs_lock:
        record = agent_runs[run_id]
        status = str(record.get("status") or "")
        if status in {"completed", "cancelled", "error"}:
            return {"run_id": run_id, "status": status}
    mark_agent_run_cancelled(run_id)
    append_agent_run_event(run_id, {
        "step": "cancelled",
        "status": "用户已终止 AI 分析",
    })
    delete_agent_run_checkpoint(run_id)
    return {"run_id": run_id, "status": "cancelled"}


@app.get("/api/agent/run/{run_id}")
def get_agent_run(run_id: str, after: int = 0):
    start = max(0, int(after or 0))
    record = get_or_load_agent_run(run_id)
    if record is None:
        snapshot = recover_latest_trace_snapshot_if_needed()
        if snapshot is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        now = dt.datetime.now().isoformat(timespec="seconds")
        with agent_runs_lock:
            agent_runs[run_id] = {
                "run_id": run_id,
                "status": "completed",
                "events": [
                    {
                        "step": "final_report",
                        "status": "已从最新 trace 恢复最终报告",
                        "run_id": run_id,
                        "event_index": 0,
                    },
                    {
                        "step": "done",
                        "status": "Agent 分析完成",
                        "run_id": run_id,
                        "snapshot": snapshot,
                        "event_index": 1,
                    },
                ],
                "snapshot": snapshot,
                "error": "",
                "started_at": now,
                "updated_at": now,
                "thread": None,
            }
            record = agent_runs[run_id]
    with agent_runs_lock:
        events = list(record.get("events", []))
        visible_events = events[start:]
        snapshot = record.get("snapshot")
        response = {
            "run_id": run_id,
            "status": record.get("status"),
            "events": visible_events,
            "next_index": start + len(visible_events),
            "snapshot": snapshot,
            "error": record.get("error", ""),
            "started_at": record.get("started_at"),
            "updated_at": record.get("updated_at"),
        }
    return {
        **response,
    }


@app.get("/api/agent/latest")
def get_latest_agent_snapshot():
    snapshot = recover_latest_trace_snapshot_if_needed() or load_latest_agent_snapshot(config)
    return {"snapshot": snapshot}

@app.get("/api/agent/history")
def get_agent_history():
    history = []
    for path in reversed(list_agent_snapshots(config)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            history.append({"path": str(path), "error": str(e)})
            continue
        history.append({
            "path": str(path),
            "generated_at": payload.get("generated_at"),
            "source": payload.get("source"),
            "model": payload.get("model"),
            "total_value": payload.get("portfolio", {}).get("total_value"),
            "position_count": payload.get("portfolio", {}).get("position_count"),
        })
    return {"history": history}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
