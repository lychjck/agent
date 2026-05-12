import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

from .models import Holding, RiskFlag, CandidateAction
from .utils import log

def agent_snapshot_dir(config: dict[str, Any]) -> Path:
    path = Path(config.get("agent", {}).get("snapshot_dir", "data/state")).expanduser()
    (path / "snapshots").mkdir(parents=True, exist_ok=True)
    return path / "snapshots"

def save_agent_snapshot(snapshot: dict[str, Any], config: dict[str, Any]) -> Path:
    generated_at = snapshot.get("generated_at", dt.datetime.now().isoformat())
    try:
        dt_obj = dt.datetime.fromisoformat(generated_at)
        prefix = dt_obj.strftime("%Y%m%d-%H%M%S")
    except ValueError:
        prefix = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    
    filename = f"{prefix}-agent-snapshot.json"
    dir_path = agent_snapshot_dir(config)
    file_path = dir_path / filename
    
    file_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Agent 快照已保存: {file_path}")
    return file_path

def list_agent_snapshots(config: dict[str, Any]) -> list[Path]:
    dir_path = agent_snapshot_dir(config)
    if not dir_path.exists():
        return []
    # 返回按名称排序的文件（前缀包含时间，因此也是按时间排序）
    return sorted([p for p in dir_path.glob("*-agent-snapshot.json") if p.is_file()])

def load_latest_agent_snapshot(config: dict[str, Any]) -> dict[str, Any] | None:
    snapshots = list_agent_snapshots(config)
    if not snapshots:
        return None
    latest_path = snapshots[-1]
    try:
        return json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"无法读取最新的 agent 快照 {latest_path}: {e}", level="WARN")
        return None

def risk_flag_id(kind: str, scope: str, target: str) -> str:
    return f"risk:{kind}:{scope}:{target}"

def candidate_action_id(action_type: str, reason_code: str, target: str) -> str:
    return f"action:{action_type}:{reason_code}:{target}"

def build_agent_snapshot(
    source: str | None,
    ledger_summary: dict[str, Any],
    holdings: list[Holding],
    classifications: dict[str, Any],
    technical_results: list[dict[str, Any]],
    summary: dict[str, Any],
    observations: list[dict[str, Any]],
    risk_flags: list[RiskFlag],
    candidate_actions: list[CandidateAction],
    agent_report: dict[str, Any],
    model: str | None,
) -> dict[str, Any]:
    from .models import holding_to_dict, risk_flag_to_dict, candidate_action_to_dict
    
    return {
        "schema_version": 1,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": source or "unknown",
        "ledger_summary": ledger_summary,
        "portfolio": summary,
        "classifications": {code: dataclasses.asdict(cls) for code, cls in classifications.items() if hasattr(cls, "__dataclass_fields__")} if classifications else {},
        "technical_results": technical_results,
        "observations": observations,
        "risk_flags": [risk_flag_to_dict(flag) for flag in risk_flags],
        "candidate_actions": [candidate_action_to_dict(action) for action in candidate_actions],
        "agent_report": agent_report,
        "model": model or "unknown"
    }

def diff_agent_snapshots(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {
        "is_first_run": previous is None,
        "portfolio_changes": {},
        "risk_changes": {"new": [], "resolved": [], "continued": [], "worsened": [], "improved": []},
        "action_changes": {"new": [], "resolved": [], "continued": []},
        "classification_changes": {"new_known": [], "now_unknown": []}
    }
    if previous is None:
        return diff
    
    # 比较总资产变化
    prev_total = previous.get("portfolio", {}).get("total_value", 0) or 0
    curr_total = current.get("portfolio", {}).get("total_value", 0) or 0
    diff["portfolio_changes"]["total_value_delta"] = curr_total - prev_total
    
    # 持仓增减
    prev_positions = {p.get("code"): p for p in previous.get("portfolio", {}).get("positions", [])}
    curr_positions = {p.get("code"): p for p in current.get("portfolio", {}).get("positions", [])}
    
    diff["portfolio_changes"]["new_positions"] = [code for code in curr_positions if code not in prev_positions]
    diff["portfolio_changes"]["exited_positions"] = [code for code in prev_positions if code not in curr_positions]
    
    # 风险变化
    prev_risks = {r.get("id"): r for r in previous.get("risk_flags", [])}
    curr_risks = {r.get("id"): r for r in current.get("risk_flags", [])}
    
    for rid, risk in curr_risks.items():
        if rid not in prev_risks:
            diff["risk_changes"]["new"].append(risk)
        else:
            prev_sev = prev_risks[rid].get("severity")
            curr_sev = risk.get("severity")
            # 简化版严重度比较：假设 high > medium > low
            sev_levels = {"low": 1, "medium": 2, "high": 3, "critical": 4}
            p_val = sev_levels.get(prev_sev, 0)
            c_val = sev_levels.get(curr_sev, 0)
            if c_val > p_val:
                diff["risk_changes"]["worsened"].append(risk)
            elif c_val < p_val:
                diff["risk_changes"]["improved"].append(risk)
            else:
                diff["risk_changes"]["continued"].append(risk)
                
    for rid, risk in prev_risks.items():
        if rid not in curr_risks:
            diff["risk_changes"]["resolved"].append(risk)
            
    # 动作变化
    prev_actions = {a.get("id"): a for a in previous.get("candidate_actions", [])}
    curr_actions = {a.get("id"): a for a in current.get("candidate_actions", [])}
    
    for aid, action in curr_actions.items():
        if aid not in prev_actions:
            diff["action_changes"]["new"].append(action)
        else:
            diff["action_changes"]["continued"].append(action)
            
    for aid, action in prev_actions.items():
        if aid not in curr_actions:
            diff["action_changes"]["resolved"].append(action)
            
    # 分类状态变化
    for code, curr_pos in curr_positions.items():
        prev_pos = prev_positions.get(code)
        if not prev_pos:
            continue
        
        prev_class = prev_pos.get("asset_class")
        curr_class = curr_pos.get("asset_class")
        
        if prev_class == "unknown" and curr_class != "unknown":
            diff["classification_changes"]["new_known"].append({"code": code, "name": curr_pos.get("name"), "class": curr_class})
        elif prev_class != "unknown" and curr_class == "unknown":
            diff["classification_changes"]["now_unknown"].append({"code": code, "name": curr_pos.get("name")})

    return diff
