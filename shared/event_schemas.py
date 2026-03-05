from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


REQUIRED_EVENT_KEYS = {
    "schema_version",
    "request_id",
    "correlation_id",
    "stage",
    "role",
    "decision",
    "timestamp",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_event(
    *,
    request_id: str,
    correlation_id: str,
    stage: str,
    role: str,
    decision: str,
    details: Optional[Dict[str, Any]] = None,
    schema_version: str = "1.0.0",
) -> Dict[str, Any]:
    return {
        "schema_version": schema_version,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "stage": stage,
        "role": role,
        "decision": decision,
        "timestamp": utc_now_iso(),
        "details": details or {},
    }


def validate_event(event: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    missing = sorted(REQUIRED_EVENT_KEYS - set(event.keys()))
    if missing:
        errors.append(f"Missing required event keys: {', '.join(missing)}")
    for key in ["request_id", "correlation_id", "stage", "role", "decision", "timestamp"]:
        value = event.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"Event field '{key}' must be a non-empty string.")
    return errors


def append_event(worklog_path: Path, event: Dict[str, Any]) -> None:
    worklog_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=True)
    with worklog_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
