from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_cognition_snapshot(
    *,
    repo_root: str,
    project_name: str,
    run_id: str,
    phase: str,
    cognition_index: Dict[str, Any],
) -> Optional[str]:
    if not repo_root or not project_name or not run_id or not phase:
        return None
    base_dir = os.path.join(repo_root, ".orchestrator", "cognition", project_name, run_id)
    os.makedirs(base_dir, exist_ok=True)
    safe_phase = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in phase).strip("_")
    safe_phase = safe_phase or "snapshot"
    out_path = os.path.join(base_dir, f"{safe_phase}.json")
    payload = {
        "generated_at": _now_iso(),
        "phase": phase,
        "cognition": cognition_index if isinstance(cognition_index, dict) else {},
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return out_path
