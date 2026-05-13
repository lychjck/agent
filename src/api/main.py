from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
import json
import httpx

from stock_assistant import (
    load_config, load_env_file, ensure_dirs, fetch_tzzb_holdings,
    llm_enabled, log, holding_to_dict, load_latest_agent_snapshot, list_agent_snapshots
)
from stock_assistant.agents.agent import run_agent_analysis, run_agent_analysis_events
from stock_assistant.agents.agent_loop import run_tool_agent_events
from stock_assistant.cli.cli import build_portfolio_profile
from stock_assistant.core.utils import config_bool
import logging
import sys

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

@app.post("/api/agent/run")
async def run_agent(req: AgentRunRequest = AgentRunRequest()):
    try:
        if req.mode == "tool_agent" or (
            req.mode == "default" and config_bool(config.get("agent", {}).get("tool_agent_default", False))
        ):
            snapshot = None
            async for event in run_tool_agent_events(
                config,
                goal=req.goal or "分析当前持仓，给出组合风险、每个 ETF 的建议和需要确认的问题",
                cached_results=req.cached_results,
                model_override=req.model,
            ):
                if event.get("step") == "error":
                    raise RuntimeError(str(event.get("error") or event.get("status") or "tool_agent failed"))
                if event.get("step") == "done":
                    snapshot = event.get("snapshot")
            if snapshot is None:
                raise RuntimeError("tool_agent 没有生成 snapshot")
            return snapshot
        return await run_agent_analysis(
            config,
            cached_results=req.cached_results,
            model_override=req.model,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e

@app.get("/api/agent/latest")
def get_latest_agent_snapshot():
    snapshot = load_latest_agent_snapshot(config)
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
