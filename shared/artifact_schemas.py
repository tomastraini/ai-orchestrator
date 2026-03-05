from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


CURRENT_SCHEMA_VERSION = "1.0.0"
REQUIRED_ARTIFACT_KEYS = {"schema_version", "request_id", "correlation_id", "generated_at"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def with_artifact_header(payload: Dict[str, Any], *, request_id: str, correlation_id: str) -> Dict[str, Any]:
    wrapped = dict(payload)
    wrapped["schema_version"] = CURRENT_SCHEMA_VERSION
    wrapped["request_id"] = request_id
    wrapped["correlation_id"] = correlation_id
    wrapped["generated_at"] = utc_now_iso()
    return wrapped


def validate_artifact(payload: Dict[str, Any]) -> List[str]:
    missing = sorted(REQUIRED_ARTIFACT_KEYS - set(payload.keys()))
    if missing:
        return [f"Artifact missing required keys: {', '.join(missing)}"]
    return []


def write_json_artifact(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
