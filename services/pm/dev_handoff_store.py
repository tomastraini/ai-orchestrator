from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_projects_path(path: str, default_path: str) -> str:
    raw = (path or "").replace("\\", "/").strip().lstrip("./")
    if not raw:
        raw = default_path
    if not raw.startswith("projects/"):
        raw = default_path
    while "/projects/" in raw:
        raw = raw.replace("/projects/", "/")
    return raw


def _normalize_execution_step(
    *,
    project_root: str,
    command: str,
    cwd: str,
    purpose: str,
) -> Dict[str, str]:
    cmd = command.strip()
    low = cmd.lower()

    # Normalize chained commands to a single executable command.
    if "&&" in cmd:
        segments = [seg.strip() for seg in cmd.split("&&") if seg.strip()]
        filtered: List[str] = []
        for seg in segments:
            lowered = seg.lower()
            if lowered.startswith("cd "):
                continue
            if lowered.startswith("mkdir ") or lowered.startswith("mkdir -p "):
                continue
            filtered.append(seg)
        cmd = filtered[0] if filtered else ""
        low = cmd.lower()

    normalized_cwd = (cwd or ".").strip()
    if normalized_cwd in {"", ".", "./"}:
        normalized_cwd = project_root
    if normalized_cwd.startswith("./"):
        normalized_cwd = normalized_cwd[2:]
    normalized_cwd = _normalize_projects_path(normalized_cwd, project_root)

    # Force non-interactive scaffold defaults.
    if "create-react-app" in low and "--use-npm" not in low:
        cmd = f"{cmd} --use-npm"
    if "nest new" in low and "@nestjs/cli" not in low:
        parts = cmd.split()
        app_name = parts[2] if len(parts) >= 3 else "back-end"
        cmd = f"npx @nestjs/cli new {app_name} --package-manager npm --skip-git"
    if "@nestjs/cli new" in low:
        if "--package-manager" not in cmd.lower():
            cmd = f"{cmd} --package-manager npm"
        if "--skip-git" not in cmd.lower():
            cmd = f"{cmd} --skip-git"

    return {
        "cwd": normalized_cwd,
        "command": cmd,
        "purpose": purpose,
    }


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
    project_root = _normalize_projects_path(project_root, project_root)

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
                _normalize_execution_step(
                    project_root=project_root,
                    cwd=str(cmd.get("cwd", ".")),
                    command=str(cmd.get("command", "")),
                    purpose=str(cmd.get("purpose", "bootstrap")),
                )
            )

    return {
        "request_id": request_id,
        "project_root": project_root,
        "structure_plan": structure_plan,
        "execution_steps": execution_steps,
        "pm_checklist": plan.get("pm_checklist", {}),
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
