from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from services.dev.types.dev_graph_state import DevGraphState


def emit_state_log(state: DevGraphState, message: str) -> None:
    state["logs"].append(message)
    sink = state.get("log_sink")
    if callable(sink):
        try:
            sink(message)
        except Exception:
            pass


def sanitize_text(value: Any, max_length: int = 600) -> str:
    text = str(value or "")
    if len(text) > max_length:
        text = f"{text[:max_length]}... [truncated {len(text) - max_length} chars]"
    patterns = [
        re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
        re.compile(r"(token\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
        re.compile(r"(password\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
    ]
    for pattern in patterns:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


def relpath_safe(state: DevGraphState, path: str) -> str:
    try:
        scope = os.path.abspath(str(state.get("scope_root", "")))
        candidate = os.path.abspath(path)
        if scope and os.path.commonpath([scope, candidate]) == scope:
            return os.path.relpath(candidate, scope).replace("\\", "/")
        return candidate.replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def emit_state_event(state: DevGraphState, category: str, **metadata: Any) -> None:
    event = {
        "timestamp_ms": int(time.time() * 1000),
        "request_id": str(state.get("request_id", "")),
        "phase": str(state.get("current_step", "")),
        "category": category,
        "metadata": metadata,
    }
    state.setdefault("telemetry_events", []).append(event)
    emit_state_log(state, f"[EVENT] {json.dumps(event, sort_keys=True)}")

