import json
import shutil
import subprocess
from typing import Any, Dict

def web_read(url: str, timeout: float = 20.0) -> Dict[str, Any]:
    """网页抓取转换 markdown"""
    opencli = shutil.which("opencli")
    if not opencli:
        return {"ok": False, "error": "opencli not found"}
    args = [opencli, "web", "read", url, "-f", "json"]
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            return {"ok": False, "error": res.stderr.strip()}
        payload = json.loads(res.stdout)
        return {"ok": True, "content": payload.get("markdown", ""), "title": payload.get("title", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
