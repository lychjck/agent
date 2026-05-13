import datetime as dt
import json
from pathlib import Path
from typing import Any

from .agent_executor import redact_sensitive
from .utils import config_bool


class AgentTraceWriter:
    def __init__(self, config: dict[str, Any], run_id: str) -> None:
        self.config = config
        self.run_id = run_id
        self.enabled = config_bool(config.get("agent", {}).get("save_traces", True))
        trace_dir = Path(config.get("agent", {}).get("trace_dir", "data/state/agent_traces")).expanduser()
        self.path = trace_dir / f"{run_id}.jsonl"
        if self.enabled:
            trace_dir.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        clean_payload = redact_sensitive(payload)
        if "type" in clean_payload:
            clean_payload["payload_type"] = clean_payload.pop("type")
        record = {
            "run_id": self.run_id,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "type": event_type,
            **clean_payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
