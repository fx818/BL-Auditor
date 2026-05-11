import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[2]
TRACES_CSV = ROOT_DIR / "audit_traces.csv"
LEGACY_TRACES_DIR = ROOT_DIR / "audit_traces"

CSV_FIELDS = [
    "trace_id",
    "offer_id",
    "item_name",
    "mcat_name",
    "started_at",
    "completed_at",
    "total_steps",
    "has_error",
    "steps_json",
]

# Some trace rows (with full LLM raw_output blobs) easily exceed the default
# 128KB cell limit on Windows. Raise the ceiling once at import time.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


_HEADER_LINE = ",".join(CSV_FIELDS).encode("utf-8")


def _ensure_csv() -> None:
    if TRACES_CSV.exists():
        _repair_csv()
        return
    TRACES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with TRACES_CSV.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
    _migrate_legacy_json()


def _repair_csv() -> None:
    """Defensively heal the CSV before reading or appending:
      1. If the header line is missing its terminator (e.g. Excel/manual edit
         that glued the header onto row 1), inject a CRLF after the header.
      2. If the file does not end with a newline, append one so the next
         row does not concatenate with the last.
    """
    size = TRACES_CSV.stat().st_size
    if size == 0:
        with TRACES_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
        return

    with TRACES_CSV.open("rb") as f:
        head = f.read(len(_HEADER_LINE) + 2)
    if head.startswith(_HEADER_LINE):
        next_byte = head[len(_HEADER_LINE):len(_HEADER_LINE) + 1]
        if next_byte not in (b"\r", b"\n", b""):
            # Header is intact but missing its \r\n — splice one in.
            data = TRACES_CSV.read_bytes()
            fixed = data[:len(_HEADER_LINE)] + b"\r\n" + data[len(_HEADER_LINE):]
            TRACES_CSV.write_bytes(fixed)

    with TRACES_CSV.open("rb") as f:
        f.seek(-1, 2)
        last = f.read(1)
    if last not in (b"\n",):
        with TRACES_CSV.open("ab") as f:
            f.write(b"\r\n")


def _append_row(row: Dict[str, Any]) -> None:
    _ensure_csv()
    with TRACES_CSV.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)


def _migrate_legacy_json() -> None:
    if not LEGACY_TRACES_DIR.exists():
        return
    rows: List[Dict[str, Any]] = []
    for path in LEGACY_TRACES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append(_data_to_row(data))
        except Exception:
            continue
    if not rows:
        return
    rows.sort(key=lambda r: r.get("started_at", ""))
    with TRACES_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        for row in rows:
            writer.writerow(row)


def _data_to_row(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trace_id": data.get("trace_id", ""),
        "offer_id": data.get("offer_id", ""),
        "item_name": data.get("item_name", "") or "",
        "mcat_name": data.get("mcat_name", "") or "",
        "started_at": data.get("started_at", ""),
        "completed_at": data.get("completed_at", ""),
        "total_steps": data.get("total_steps", 0),
        "has_error": "true" if data.get("has_error") else "false",
        "steps_json": json.dumps(data.get("steps", []), ensure_ascii=False, default=str),
    }


def _row_to_summary(row: Dict[str, str]) -> Dict[str, Any]:
    started = row.get("started_at", "") or ""
    completed = row.get("completed_at", "") or ""
    duration_ms = 0
    if started and completed:
        try:
            duration_ms = int((datetime.fromisoformat(completed) - datetime.fromisoformat(started)).total_seconds() * 1000)
        except Exception:
            duration_ms = 0
    try:
        total_steps = int(row.get("total_steps") or 0)
    except (TypeError, ValueError):
        total_steps = 0
    return {
        "trace_id": row.get("trace_id", ""),
        "offer_id": row.get("offer_id", ""),
        "item_name": row.get("item_name", "") or "",
        "mcat_name": row.get("mcat_name", "") or "",
        "started_at": started[:19].replace("T", " ") if started else "",
        "total_steps": total_steps,
        "has_error": (row.get("has_error") or "").lower() == "true",
        "duration_ms": duration_ms,
    }


def _row_to_full(row: Dict[str, str]) -> Dict[str, Any]:
    summary = _row_to_summary(row)
    try:
        steps = json.loads(row.get("steps_json") or "[]")
    except json.JSONDecodeError:
        steps = []
    started = row.get("started_at", "") or ""
    completed = row.get("completed_at", "") or ""
    return {
        "trace_id": summary["trace_id"],
        "offer_id": summary["offer_id"],
        "item_name": summary["item_name"],
        "mcat_name": summary["mcat_name"],
        "started_at": started,
        "completed_at": completed,
        "total_steps": summary["total_steps"],
        "has_error": summary["has_error"],
        "steps": steps,
        "duration_ms": summary["duration_ms"],
        "started_at_display": summary["started_at"],
    }


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
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        trace_id = f"{self.offer_id}_{ts}"
        row = _data_to_row({
            "trace_id": trace_id,
            "offer_id": self.offer_id,
            "item_name": item_name,
            "mcat_name": mcat_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": datetime.now().isoformat(),
            "total_steps": self._step,
            "has_error": any(s["status"] == "error" for s in self._steps),
            "steps": self._steps,
        })
        _append_row(row)
        return trace_id


def list_traces() -> List[Dict[str, Any]]:
    _ensure_csv()
    result: List[Dict[str, Any]] = []
    with TRACES_CSV.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result.append(_row_to_summary(row))
    result.sort(key=lambda r: r.get("trace_id", ""), reverse=True)
    return result


def get_trace(trace_id: str) -> Optional[Dict[str, Any]]:
    _ensure_csv()
    with TRACES_CSV.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("trace_id") == trace_id:
                return _row_to_full(row)
    return None
