import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

TRACES_DIR = Path(__file__).resolve().parents[2] / "audit_traces"


class AuditTrace:
    def __init__(self, offer_id: str):
        self.offer_id = offer_id
        self.started_at = datetime.now()
        self._steps: List[Dict[str, Any]] = []
        self._step = 0

    def add_step(
        self,
        name: str,
        type_: str,
        *,
        endpoint: str = "",
        input_: Any = None,
        output: Any = None,
        raw_output: str = "",
        parsed: Any = None,
        error: Any = None,
        duration_ms: int = 0,
        llm_messages: Optional[Dict[str, str]] = None,
        sub_steps: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self._step += 1
        step: Dict[str, Any] = {
            "step": self._step,
            "name": name,
            "type": type_,
            "timestamp": datetime.now().isoformat(),
            "status": "error" if error else "success",
            "duration_ms": duration_ms,
        }
        if endpoint:
            step["endpoint"] = endpoint
        if input_ is not None:
            step["input"] = input_
        if output is not None:
            step["output"] = output
        if raw_output:
            step["raw_output"] = raw_output
        if parsed is not None:
            step["parsed"] = parsed
        if error:
            step["error"] = str(error)
        if llm_messages:
            step["llm_messages"] = llm_messages
        if sub_steps:
            step["sub_steps"] = sub_steps
        self._steps.append(step)

    @property
    def steps(self) -> List[Dict[str, Any]]:
        return self._steps

    def save(self, item_name: str = "", mcat_name: str = "") -> str:
        TRACES_DIR.mkdir(exist_ok=True)
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        trace_id = f"{self.offer_id}_{ts}"
        data = {
            "trace_id": trace_id,
            "offer_id": self.offer_id,
            "item_name": item_name,
            "mcat_name": mcat_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": datetime.now().isoformat(),
            "total_steps": self._step,
            "has_error": any(s["status"] == "error" for s in self._steps),
            "steps": self._steps,
        }
        path = TRACES_DIR / f"{trace_id}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return trace_id


def list_traces() -> List[Dict[str, Any]]:
    if not TRACES_DIR.exists():
        return []
    result = []
    for f in sorted(TRACES_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            started = data.get("started_at", "")
            completed = data.get("completed_at", "")
            duration_ms = 0
            if started and completed:
                try:
                    from datetime import datetime as dt
                    d = (dt.fromisoformat(completed) - dt.fromisoformat(started)).total_seconds()
                    duration_ms = int(d * 1000)
                except Exception:
                    pass
            result.append({
                "trace_id": data["trace_id"],
                "offer_id": data["offer_id"],
                "item_name": data.get("item_name", ""),
                "mcat_name": data.get("mcat_name", ""),
                "started_at": started[:19].replace("T", " ") if started else "",
                "total_steps": data.get("total_steps", 0),
                "has_error": data.get("has_error", False),
                "duration_ms": duration_ms,
            })
        except Exception:
            pass
    return result


def get_trace(trace_id: str) -> Optional[Dict[str, Any]]:
    path = TRACES_DIR / f"{trace_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    started = data.get("started_at", "")
    completed = data.get("completed_at", "")
    duration_ms = 0
    if started and completed:
        try:
            from datetime import datetime as dt
            d = (dt.fromisoformat(completed) - dt.fromisoformat(started)).total_seconds()
            duration_ms = int(d * 1000)
        except Exception:
            pass
    data["duration_ms"] = duration_ms
    data["started_at_display"] = started[:19].replace("T", " ") if started else ""
    return data
