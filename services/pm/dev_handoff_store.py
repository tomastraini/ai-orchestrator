from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_dev_handoff(
    *,
    request_id: str,
    plan: Dict[str, Any],
    rounds: List[Dict[str, str]],
) -> Dict[str, Any]:
    project_ref = plan.get("project_ref", {})
    project_root = project_ref.get("path_hint") if isinstance(project_ref, dict) else None
    if not isinstance(project_root, str) or not project_root.strip():
        project_name = project_ref.get("name", "project") if isinstance(project_ref, dict) else "project"
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(project_name))
        slug = "-".join(part for part in slug.split("-") if part) or "project"
        project_root = f"projects/{slug}"

    structure_plan = [
        {"path": f"{project_root}/front-end", "kind": "suggested"},
        {"path": f"{project_root}/back-end", "kind": "suggested"},
    ]
    # include optional database folder if PM hinted it
    all_text = " ".join(
        [
            str(plan.get("summary", "")),
            " ".join(str(x) for x in plan.get("constraints", [])),
            " ".join(str(x) for x in plan.get("validation", [])),
        ]
    ).lower()
    if "database" in all_text or "sql" in all_text or "query" in all_text:
        structure_plan.append({"path": f"{project_root}/database", "kind": "optional"})

    execution_steps: List[Dict[str, str]] = []
    for cmd in plan.get("bootstrap_commands", []):
        if isinstance(cmd, dict):
            execution_steps.append(
                {
                    "cwd": str(cmd.get("cwd", ".")),
                    "command": str(cmd.get("command", "")),
                    "purpose": str(cmd.get("purpose", "bootstrap")),
                }
            )

    return {
        "request_id": request_id,
        "project_root": project_root,
        "structure_plan": structure_plan,
        "execution_steps": execution_steps,
        "constraints": [str(x) for x in plan.get("constraints", [])],
        "validation": [str(x) for x in plan.get("validation", [])],
        "clarifications": rounds,
        "generated_at": _utc_now_iso(),
    }


class DevHandoffStore:
    def __init__(self, repo_root: str, relative_store_path: str = ".orchestrator/dev_handoff.json"):
        self.repo_root = repo_root
        self.store_path = os.path.join(repo_root, relative_store_path)

    def write_latest(self, handoff: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump({"latest_handoff": handoff}, fh, indent=2)
