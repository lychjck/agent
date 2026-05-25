import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Dict

from stock_mcp.core.logging import logger

def get_snapshots_dir(config: Dict[str, Any]) -> Path:
    d = Path(config.get("paths", {}).get("snapshots_dir", "./data/snapshots"))
    d.mkdir(parents=True, exist_ok=True)
    return d

def load_latest_snapshot(config: Dict[str, Any]) -> Dict[str, Any] | None:
    snap_dir = get_snapshots_dir(config)
    files = sorted(snap_dir.glob("*-snapshot.json"))
    if not files:
        return None
    latest = files[-1]
    return json.loads(latest.read_text(encoding="utf-8"))

def save_snapshot_to_file(config: Dict[str, Any], snapshot_data: Dict[str, Any]) -> tuple[str, str]:
    snap_dir = get_snapshots_dir(config)
    today_str = dt.date.today().isoformat()
    filename = f"{today_str}-snapshot.json"
    filepath = snap_dir / filename
    
    payload = dict(snapshot_data)
    payload["generated_at"] = dt.datetime.now().isoformat()
    
    filepath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Successfully archived snapshot: {filepath}")
    
    # 180天滚动清理
    limit_time = time.time() - 180 * 86400
    for f in snap_dir.glob("*-snapshot.json"):
        if f.stat().st_mtime < limit_time:
            try:
                f.unlink()
                logger.info(f"Unlinked expired snapshot: {f.name}")
            except Exception:
                pass
                
    return filename, str(filepath)
