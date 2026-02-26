from __future__ import annotations

import os
import json
from typing import Any, Dict

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None

try:
    import zstandard as zstd
except Exception:  # pragma: no cover - optional dependency
    zstd = None


def _build_handoff_pack(handoff: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema": "HandoffPackYAML/v1",
        "meta": {
            "request_id": str(handoff.get("request_id", "")),
            "generated_at": str(handoff.get("generated_at", "")),
        },
        "goal": {
            "constraints": [str(x) for x in handoff.get("constraints", [])],
            "validation": [str(x) for x in handoff.get("validation", [])],
        },
        "workspace": {
            "project_root": str(handoff.get("project_root", "")),
            "workspace_context": handoff.get("workspace_context", {}),
            "cognition_snapshot": handoff.get("cognition_snapshot", {}),
            "path_aliases": handoff.get("path_aliases", {}),
        },
        "roles": {
            "pm_checklist": handoff.get("pm_checklist", {}),
            "clarifications": handoff.get("clarifications", []),
        },
        "execution": {
            "steps": handoff.get("execution_steps", []),
            "structure_plan": handoff.get("structure_plan", []),
            "target_file_metadata": handoff.get("target_file_metadata", []),
            "command_policy": handoff.get("command_policy", {}),
            "internal_checklist": handoff.get("internal_checklist", []),
            "task_outcomes": handoff.get("task_outcomes", []),
        },
        "resume": {
            "selected_project_root": str(handoff.get("selected_project_root", "")),
            "workspace_snapshot_hash": str(handoff.get("workspace_snapshot_hash", "")),
            "pending_tasks": handoff.get("pending_tasks", []),
            "checkpoints": handoff.get("checkpoints", []),
            "checklist_cursor": str(handoff.get("checklist_cursor", "")),
        },
        "memory": handoff.get("memory", {}),
        "dev_preflight_plan": handoff.get("dev_preflight_plan", {}),
    }


def write_handoffpack_yaml(base_path: str, handoff: Dict[str, Any]) -> str:
    os.makedirs(os.path.dirname(base_path), exist_ok=True)
    payload = _build_handoff_pack(handoff)
    yaml_path = f"{base_path}.yaml"
    with open(yaml_path, "w", encoding="utf-8") as fh:
        if yaml is not None:
            yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=False)
        else:
            fh.write(json.dumps(payload, indent=2))
    if zstd is not None:
        zstd_path = f"{yaml_path}.zst"
        cctx = zstd.ZstdCompressor(level=7)
        with open(yaml_path, "rb") as src, open(zstd_path, "wb") as dst:
            dst.write(cctx.compress(src.read()))
    return yaml_path

