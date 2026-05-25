import datetime as dt
import json
import shutil
import subprocess
from typing import Any, Dict, List

from stock_mcp.core import logger

def run_opencli_command(site: str, command: str, positionals: List[str], options: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
    opencli = shutil.which("opencli")
    if not opencli:
        return {"ok": False, "error": "opencli not found on host"}
    
    args = [opencli, site, command] + positionals + ["-f", "json"]
    for k, v in options.items():
        args.extend([f"--{k}", str(v)])
    
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            return {"ok": False, "error": res.stderr.strip()}
        try:
            return {"ok": True, "data": json.loads(res.stdout)}
        except json.JSONDecodeError:
            return {"ok": True, "raw_data": res.stdout.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def web_search(query: str, max_results: int = 5, timeout: float = 20.0) -> List[Dict[str, str]]:
    opencli = shutil.which("opencli")
    if not opencli:
        logger.error("opencli command not found on host")
        return []
    
    args = [opencli, "duckduckgo", "search", query, "--limit", str(max_results), "-f", "json", "--window", "background"]
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            logger.error(f"opencli search failed: {res.stderr}")
            return []
        payload = json.loads(res.stdout)
        rows = payload if isinstance(payload, list) else payload.get("rows", [])
        
        results = []
        retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
        for row in rows[:max_results]:
            if not isinstance(row, dict):
                continue
            snippet = str(row.get("snippet") or row.get("summary") or row.get("content") or "")
            results.append({
                "title": str(row.get("title") or row.get("name") or ""),
                "url": str(row.get("url") or row.get("link") or ""),
                "snippet": snippet,
                "content": snippet,
                "source": "opencli:duckduckgo",
                "retrieved_at": retrieved_at,
            })
        return results
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return []
