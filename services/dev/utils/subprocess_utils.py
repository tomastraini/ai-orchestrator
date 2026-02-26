from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional


SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
]


def emit_log(logs: List[str], message: str, log_sink: Optional[Callable[[str], None]]) -> None:
    logs.append(message)
    if callable(log_sink):
        try:
            log_sink(message)
        except Exception:
            pass


def sanitize_log_value(value: Any, max_length: int = 500) -> str:
    text = str(value or "")
    if len(text) > max_length:
        text = f"{text[:max_length]}... [truncated {len(text) - max_length} chars]"
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


def emit_executor_event(
    logs: List[str],
    event: Dict[str, Any],
    log_sink: Optional[Callable[[str], None]],
    event_sink: Optional[Callable[[Dict[str, Any]], None]],
) -> None:
    payload = dict(event)
    emit_log(logs, f"[EVENT] {json.dumps(payload, sort_keys=True)}", log_sink)
    if callable(event_sink):
        try:
            event_sink(payload)
        except Exception:
            pass

