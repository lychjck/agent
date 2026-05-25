import datetime as dt
from typing import Any, Dict
from pydantic import BaseModel, Field

from stock_mcp.registry import registry
from stock_mcp.context import ToolContext
from stock_mcp.persistence.snapshots import (
    load_latest_snapshot,
    save_snapshot_to_file,
    get_snapshots_dir
)

class SnapshotSummaryArgs(BaseModel):
    which: str = Field("latest", description="指定读取的快照，如 'latest'")

class CompareSnapshotsArgs(BaseModel):
    current_facts: Dict[str, Any] = Field(..., description="当前大礼包持仓数据")
    previous: str = Field("latest", description="历史基准快照版本")

class SaveSnapshotArgs(BaseModel):
    snapshot_data: Dict[str, Any] = Field(..., description="要保存的快照核心数据")

@registry.register("load_snapshot_summary", "加载并获取历史最新的资产诊断快照摘要", SnapshotSummaryArgs)
def load_snapshot_summary(args: SnapshotSummaryArgs, ctx: ToolContext) -> dict:
    try:
        data = load_latest_snapshot(ctx.config)
        if not data:
            return {"ok": False, "error_type": "no_snapshot", "message": "尚未保存任何历史快照"}
            
        snap_dir = get_snapshots_dir(ctx.config)
        files = sorted(snap_dir.glob("*-snapshot.json"))
        filename = files[-1].name if files else ""
        
        holdings = data.get("holdings", [])
        profile = data.get("portfolio_profile", {})
        return {
            "ok": True,
            "filename": filename,
            "generated_at": data.get("generated_at", ""),
            "portfolio_top": [h.get("name", h.get("code", "")) for h in holdings[:3]],
            "risk_count": len(profile.get("observations", [])),
            "total_value": profile.get("total_value", 0.0)
        }
    except Exception as e:
        return {"ok": False, "error_type": "load_error", "message": str(e)}

@registry.register("save_snapshot", "显式保存当天最新的投资诊断事实快照", SaveSnapshotArgs)
def save_snapshot(args: SaveSnapshotArgs, ctx: ToolContext) -> dict:
    try:
        filename, filepath = save_snapshot_to_file(ctx.config, args.snapshot_data)
        return {"ok": True, "filename": filename, "filepath": filepath}
    except Exception as e:
        return {"ok": False, "error_type": "save_error", "message": str(e)}

@registry.register("compare_snapshots", "无状态比对当前事实与历史最新快照的变动Diff", CompareSnapshotsArgs)
def compare_snapshots(args: CompareSnapshotsArgs, ctx: ToolContext) -> dict:
    try:
        prev_data = load_latest_snapshot(ctx.config)
        if not prev_data:
            return {"ok": True, "diff": {}, "message": "尚未发现任何历史快照对比基准"}
            
        snap_dir = get_snapshots_dir(ctx.config)
        files = sorted(snap_dir.glob("*-snapshot.json"))
        compared_with = files[-1].name if files else "unknown"
        
        prev_holdings = {h["code"]: h for h in prev_data.get("holdings", [])}
        curr_holdings = {h["code"]: h for h in args.current_facts.get("holdings", [])}
        
        added = []
        removed = []
        changed = []
        
        for code, h in curr_holdings.items():
            if code not in prev_holdings:
                added.append({"code": code, "name": h.get("name", code), "value": h.get("value", 0.0)})
            else:
                p_h = prev_holdings[code]
                v_diff = h.get("value", 0.0) - p_h.get("value", 0.0)
                if abs(v_diff) > 0.01:
                    changed.append({"code": code, "name": h.get("name", code), "value_delta": v_diff})
                    
        for code, h in prev_holdings.items():
            if code not in curr_holdings:
                removed.append({"code": code, "name": h.get("name", code), "value": h.get("value", 0.0)})
                
        prev_observations = set(prev_data.get("portfolio_profile", {}).get("observations", []))
        curr_observations = set(args.current_facts.get("portfolio_profile", {}).get("observations", []))
        
        new_risks = list(curr_observations - prev_observations)
        resolved_risks = list(prev_observations - curr_observations)
        
        return {
            "ok": True,
            "compared_with": compared_with,
            "diff": {
                "holdings_added": added,
                "holdings_removed": removed,
                "holdings_value_changed": changed,
                "new_risks": new_risks,
                "resolved_risks": resolved_risks
            }
        }
    except Exception as e:
        return {"ok": False, "error_type": "compare_error", "message": str(e)}

