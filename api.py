from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import glob
from pathlib import Path
import json
import httpx
import datetime as dt

from stock_assistant import (
    load_config, load_env_file, ensure_dirs, fetch_tzzb_holdings,
    analyze_holdings, llm_enabled, generate_structured_llm_commentary, log,
    fetch_bars, analyze_one, holding_to_dict, analysis_result_to_dict
)
from stock_assistant.cli import build_portfolio_profile
from stock_assistant.utils import setup_basic_logging

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
            holdings_file=None,
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
        from stock_assistant.cli import load_profile_holdings
        from stock_assistant.search import suggest_classification_with_search
        
        holdings, _, _ = load_profile_holdings(config, holdings_file=None)
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

@app.post("/api/analyze")
async def analyze_portfolio(req: AnalyzeRequest = AnalyzeRequest()):
    if not llm_enabled(config):
        raise HTTPException(status_code=400, detail="LLM 未启用，请在 config.toml 中配置")
    
    async def event_generator():
        try:
            results = []
            current_model = req.model or config.get("llm", {}).get("model", "unknown")
            
            if req.cached_results:
                msg = f"检测到已有的技术分析数据，跳过行情拉取，直接进入 AI 诊断 (模型: {current_model})..."
                log(msg)
                yield f"data: {json.dumps({'status': msg, 'step': 3})}\n\n"
                results = req.cached_results
            elif str(config.get("ledger", {}).get("mode", "")).strip().lower() == "tzzb_api":
                log("正在连接投资账本并同步持仓数据...")
                yield f"data: {json.dumps({'status': '正在连接投资账本并同步持仓数据...', 'step': 1})}\n\n"
                holdings, _, summary = fetch_tzzb_holdings(config)
                
                log(f"已获取 {len(holdings)} 个标的，开始拉取行情并进行技术面分析...")
                yield f"data: {json.dumps({'status': f'已获取 {len(holdings)} 个标的，开始拉取行情并进行技术面分析...', 'step': 2})}\n\n"
                
                total_value = sum(item.market_value or 0 for item in holdings) or None
                for i, holding in enumerate(holdings):
                    if holding.asset_type == "fund":
                        res = {
                            "holding": holding_to_dict(holding), 
                            "ok": True, "action": "持有场外基金", "reason": "场外基金，不参与K线分析",
                            "profit_pct": holding.profit_pct, "current_value": holding.market_value,
                            "weight": holding.market_value / total_value * 100 if holding.market_value and total_value else None
                        }
                        results.append(res)
                        continue
                    
                    try:
                        msg = f"[{i+1}/{len(holdings)}] 正在拉取 {holding.name} ({holding.code}) 的行情数据..."
                        log(msg)
                        yield f"data: {json.dumps({'status': msg, 'step': 2})}\n\n"
                        bars = fetch_bars(holding.code, config)
                        analysis_res = analyze_one(holding, bars, config, total_value)
                        
                        serializable_res = analysis_result_to_dict(analysis_res)
                        results.append(serializable_res)
                    except Exception as e:
                        log(f"分析 {holding.code} 失败: {e}")
                        results.append({
                            "holding": holding_to_dict(holding), 
                            "ok": False, "action": "行情失败", "reason": str(e)
                        })
                
                # 发送中间结果
                log("技术分析完成，已暂存中间数据。")
                yield f"data: {json.dumps({'status': '技术分析完成，已暂存中间数据。', 'step': 2, 'technical_results': results})}\n\n"
            else:
                log("错误: 当前仅支持 tzzb_api 模式。")
                yield f"data: {json.dumps({'error': '当前仅支持 tzzb_api 模式。'})}\n\n"

            if results:
                msg = f"正在准备历史快照上下文并请求深度诊断 ({current_model})..."
                log(msg)
                yield f"data: {json.dumps({'status': msg, 'step': 3})}\n\n"
                
                # --- Task 8 Memory Integration ---
                snapshot_diff = None
                snapshot_to_save = None
                try:
                    from stock_assistant.cli import profile_classification_for_holding
                    from stock_assistant.portfolio import summarize_portfolio, generate_portfolio_observations
                    from stock_assistant.memory import load_latest_agent_snapshot, diff_agent_snapshots, build_agent_snapshot, save_agent_snapshot
                    from stock_assistant.models import Holding
                    
                    _holdings = []
                    if not req.cached_results and 'holdings' in locals():
                        _holdings = holdings
                    else:
                        valid_keys = {"code", "name", "quantity", "cost_price", "market_value", "profit_pct", "hold_profit", "day_profit", "asset_type"}
                        for res in results:
                            h_data = res.get("holding", {})
                            h_kwargs = {k: v for k, v in h_data.items() if k in valid_keys}
                            if "code" in h_kwargs and "name" in h_kwargs:
                                _holdings.append(Holding(**h_kwargs))
                                
                    classifications = {
                        h.code: profile_classification_for_holding(h, config, False)
                        for h in _holdings
                    }
                    portfolio_summary = summarize_portfolio(_holdings, classifications, config)
                    observations = generate_portfolio_observations(portfolio_summary)
                    risk_flags = []
                    candidate_actions = []
                    
                    previous_snapshot = load_latest_agent_snapshot(config)
                    current_state = {
                        "portfolio": portfolio_summary,
                        "risk_flags": risk_flags,
                        "candidate_actions": candidate_actions
                    }
                    snapshot_diff = diff_agent_snapshots(previous_snapshot, current_state)
                    
                    snapshot_to_save = {
                        "source": "tzzb_api",
                        "ledger_summary": summary if 'summary' in locals() else {},
                        "holdings": _holdings,
                        "classifications": classifications,
                        "technical_results": results,
                        "summary": portfolio_summary,
                        "observations": observations,
                        "risk_flags": risk_flags,
                        "candidate_actions": candidate_actions,
                        "model": current_model,
                    }
                except Exception as mem_err:
                    log(f"构建快照上下文失败: {mem_err}", level="ERROR")
                # ---------------------------------
                
                commentary = generate_structured_llm_commentary(
                    results, config, model_override=req.model, snapshot_diff=snapshot_diff
                )
                
                result_data = None
                if isinstance(commentary, str):
                    clean_json = commentary.strip()
                    if clean_json.startswith("```json"):
                        clean_json = clean_json[7:-3].strip()
                    elif clean_json.startswith("```"):
                        clean_json = clean_json[3:-3].strip()
                    result_data = json.loads(clean_json)
                else:
                    result_data = commentary
                
                log("诊断报告生成完毕！")
                yield f"data: {json.dumps({'status': '诊断报告生成完毕！', 'step': 4, 'result': result_data})}\n\n"
                
                if snapshot_to_save and result_data:
                    try:
                        snapshot = build_agent_snapshot(
                            agent_report=result_data,
                            **snapshot_to_save
                        )
                        if str(config.get("agent", {}).get("save_snapshots", "true")).lower() in {"true", "1", "yes"}:
                            save_agent_snapshot(snapshot, config)
                    except Exception as e:
                        log(f"保存快照失败: {e}", level="ERROR")
                        
                # 自动保存报告到本地
                try:
                    report_dir = Path(config.get("paths", {}).get("report_dir", "reports")).expanduser()
                    report_dir.mkdir(parents=True, exist_ok=True)
                    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                    filename = f"ai-report-{timestamp}.json"
                    report_path = report_dir / filename
                    with open(report_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "model": current_model,
                            "results": results,
                            "ai_response": result_data
                        }, f, ensure_ascii=False, indent=2)
                    log(f"AI 诊断报告已保存至: {report_path}")
                except Exception as save_err:
                    log(f"保存报告失败: {save_err}")
        except Exception as e:
            log(f"分析失败: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
