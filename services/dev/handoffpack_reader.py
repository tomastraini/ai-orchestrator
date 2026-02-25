from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None


def load_handoff_from_yaml(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        if yaml is not None:
            payload = yaml.safe_load(fh) or {}
        else:
            payload = json.loads(fh.read() or "{}")
    if not isinstance(payload, dict):
        return None
    if str(payload.get("schema", "")).strip() != "HandoffPackYAML/v1":
        return None
    workspace = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
    roles = payload.get("roles") if isinstance(payload.get("roles"), dict) else {}
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    resume = payload.get("resume") if isinstance(payload.get("resume"), dict) else {}
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return {
        "request_id": str(meta.get("request_id", "")),
        "generated_at": str(meta.get("generated_at", "")),
        "project_root": str(workspace.get("project_root", "")),
        "workspace_context": workspace.get("workspace_context", {}),
        "path_aliases": workspace.get("path_aliases", {}),
        "pm_checklist": roles.get("pm_checklist", {}),
        "clarifications": roles.get("clarifications", []),
        "execution_steps": execution.get("steps", []),
        "structure_plan": execution.get("structure_plan", []),
        "command_policy": execution.get("command_policy", {}),
        "selected_project_root": str(resume.get("selected_project_root", "")),
        "workspace_snapshot_hash": str(resume.get("workspace_snapshot_hash", "")),
        "pending_tasks": resume.get("pending_tasks", []),
        "constraints": goal.get("constraints", []),
        "validation": goal.get("validation", []),
        "memory": payload.get("memory", {}),
    }


def load_handoff_with_fallback(json_store_path: str) -> Optional[Dict[str, Any]]:
    yaml_path = json_store_path.replace(".json", ".yaml")
    yaml_handoff = load_handoff_from_yaml(yaml_path)
    if yaml_handoff:
        return yaml_handoff
    if not os.path.exists(json_store_path):
        return None
    with open(json_store_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        return None
    latest = payload.get("latest_handoff")
    return latest if isinstance(latest, dict) else None

