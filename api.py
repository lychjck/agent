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
    fetch_bars, analyze_one
)

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
    # 尝试从 tzzb API 抓取最新数据或从本地归档读取
    try:
        # 这里为了演示和实时性，我们可以直接调用抓取逻辑，如果配置为tzzb_api
        if str(config.get("ledger", {}).get("mode", "")).strip().lower() == "tzzb_api":
            holdings, archived = fetch_tzzb_holdings(config)
            
            serializable_results = []
            for h in holdings:
                serializable_results.append({
                    "code": h.code,
                    "name": h.name,
                    "quantity": h.quantity,
                    "cost_price": h.cost_price,
                    "market_value": h.market_value,
                    "profit_pct": h.profit_pct,
                    "asset_type": h.asset_type,
                    "weight": (h.market_value / sum(item.market_value or 0 for item in holdings) * 100) if h.market_value and any(item.market_value for item in holdings) else None
                })
            
            # 返回总资产和持仓明细
            total_value = sum(item.market_value or 0 for item in holdings)
            total_profit = sum((item.market_value or 0) - (item.cost_price or 0) * (item.quantity or 0) for item in holdings if item.cost_price and item.quantity)
            
            return {
                "total_value": total_value,
                "total_profit": total_profit,
                "holdings": serializable_results
            }
        else:
            return {"error": "当前仅支持 tzzb_api 模式作为后端源。请在 config.toml 设置 [ledger] mode='tzzb_api'"}
    except Exception as e:
        log(f"获取持仓失败: {e}")
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
                holdings, _ = fetch_tzzb_holdings(config)
                
                log(f"已获取 {len(holdings)} 个标的，开始拉取行情并进行技术面分析...")
                yield f"data: {json.dumps({'status': f'已获取 {len(holdings)} 个标的，开始拉取行情并进行技术面分析...', 'step': 2})}\n\n"
                
                total_value = sum(item.market_value or 0 for item in holdings) or None
                for i, holding in enumerate(holdings):
                    if holding.asset_type == "fund":
                        res = {
                            "holding": {
                                "code": holding.code, "name": holding.name, "quantity": holding.quantity,
                                "cost_price": holding.cost_price, "market_value": holding.market_value,
                                "profit_pct": holding.profit_pct, "asset_type": holding.asset_type
                            }, 
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
                        
                        # 转换 Holding 对象为 dict
                        serializable_res = analysis_res.copy()
                        serializable_res["holding"] = {
                            "code": holding.code, "name": holding.name, "quantity": holding.quantity,
                            "cost_price": holding.cost_price, "market_value": holding.market_value,
                            "profit_pct": holding.profit_pct, "asset_type": holding.asset_type
                        }
                        # 处理 Bar 对象
                        if "latest" in serializable_res and hasattr(serializable_res["latest"], "date"):
                            latest = serializable_res["latest"]
                            serializable_res["latest"] = {
                                "date": str(latest.date), "close": latest.close, "pct_change": latest.pct_change
                            }
                        results.append(serializable_res)
                    except Exception as e:
                        log(f"分析 {holding.code} 失败: {e}")
                        results.append({
                            "holding": {"code": holding.code, "name": holding.name, "asset_type": holding.asset_type}, 
                            "ok": False, "action": "行情失败", "reason": str(e)
                        })
                
                # 发送中间结果
                log("技术分析完成，已暂存中间数据。")
                yield f"data: {json.dumps({'status': '技术分析完成，已暂存中间数据。', 'step': 2, 'technical_results': results})}\n\n"
            else:
                log("错误: 当前仅支持 tzzb_api 模式。")
                yield f"data: {json.dumps({'error': '当前仅支持 tzzb_api 模式。'})}\n\n"

            if results:
                msg = f"正在请求大模型进行深度诊断 ({current_model})..."
                log(msg)
                yield f"data: {json.dumps({'status': msg, 'step': 3})}\n\n"
                commentary = generate_structured_llm_commentary(results, config, model_override=req.model)
                
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
