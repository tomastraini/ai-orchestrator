from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional


PROMPT_REGEX = re.compile(
    r"(ok to proceed\??|proceed\??|\[y/n\]|\(y/n\)|\(y/N\)|\(Y/n\)|confirm\??)",
    re.IGNORECASE,
)
SERVICE_READY_REGEX = re.compile(
    r"(ready|localhost:|listening on|started|running at|server)",
    re.IGNORECASE,
)
SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
]


def _emit(logs: List[str], message: str, log_sink: Optional[Callable[[str], None]]) -> None:
    logs.append(message)
    if callable(log_sink):
        try:
            log_sink(message)
        except Exception:
            # Log streaming should never break execution.
            pass

def _sanitize_log_value(value: Any, max_length: int = 500) -> str:
    text = str(value or "")
    if len(text) > max_length:
        text = f"{text[:max_length]}... [truncated {len(text) - max_length} chars]"
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text

def _emit_event(
    logs: List[str],
    event: Dict[str, Any],
    log_sink: Optional[Callable[[str], None]],
    event_sink: Optional[Callable[[Dict[str, Any]], None]],
) -> None:
    payload = dict(event)
    _emit(logs, f"[EVENT] {json.dumps(payload, sort_keys=True)}", log_sink)
    if callable(event_sink):
        try:
            event_sink(payload)
        except Exception:
            pass
