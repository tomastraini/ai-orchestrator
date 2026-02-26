from __future__ import annotations

import json
import os
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from services.pm.handoffpack_writer import write_handoffpack_yaml
from services.workspace.project_index import scan_projects_root
from shared.pathing import canonical_projects_path, normalize_rel_path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_projects_path(path: str, default_path: str) -> str:
    return canonical_projects_path(path, default_path)


def _contains_path_marker(path: str, markers: List[str]) -> bool:
    low = path.replace("\\", "/").lower()
    return any(marker in low for marker in markers)


def _derive_structure_plan(project_root: str, plan: Dict[str, Any]) -> List[Dict[str, str]]:
    targets = plan.get("target_files") if isinstance(plan.get("target_files"), list) else []
    structure_paths: List[str] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        expected_path = str(target.get("expected_path_hint", "")).strip()
        if expected_path:
            normalized = _normalize_projects_path(expected_path, f"{project_root}/{expected_path}")
            parent = normalized.rsplit("/", 1)[0] if "/" in normalized else normalized
            if parent and parent not in structure_paths:
                structure_paths.append(parent)
    if not structure_paths:
        structure_paths = [project_root]
    return [{"path": path, "kind": "required"} for path in structure_paths]


def _normalize_execution_step(
    *,
    project_root: str,
    command: str,
    cwd: str,
    purpose: str,
) -> Dict[str, str]:
    def _relativize_scaffold_target(token: str, cwd_norm: str, project_root_norm: str) -> str:
        target = token.strip().replace("\\", "/").lstrip("./")
        project_name = project_root_norm.split("/")[-1] if project_root_norm else ""
        cwd_name = cwd_norm.split("/")[-1] if cwd_norm else ""
        if not target.startswith("projects/"):
            if project_name and cwd_norm == project_root_norm and target == project_name:
                return "."
            if cwd_name and target == cwd_name:
                return "."
            return token
        if target == cwd_norm or target == project_root_norm:
            return "."
        if cwd_norm and target.startswith(f"{cwd_norm}/"):
            return target[len(cwd_norm) + 1 :] or "."
        if project_root_norm and target.startswith(f"{project_root_norm}/") and cwd_norm == project_root_norm:
            return target[len(project_root_norm) + 1 :] or "."
        return token

    def _normalize_scaffold_target_arg(cmd_str: str, cwd_norm: str, project_root_norm: str) -> str:
        tokens = cmd_str.split()
        low_tokens = [t.lower() for t in tokens]
        if not tokens:
            return cmd_str

        # create-react-app <target>
        if any("create-react-app" in t for t in low_tokens):
            for i, tok in enumerate(low_tokens):
                if "create-react-app" in tok and len(tokens) > i + 1:
                    tokens[i + 1] = _relativize_scaffold_target(tokens[i + 1], cwd_norm, project_root_norm)
                    return " ".join(tokens)

        # create-vite <target> OR npm create vite@latest <target>
        if any("create-vite" in t for t in low_tokens):
            for i, tok in enumerate(low_tokens):
                if "create-vite" in tok and len(tokens) > i + 1 and not tokens[i + 1].startswith("-"):
                    tokens[i + 1] = _relativize_scaffold_target(tokens[i + 1], cwd_norm, project_root_norm)
                    return " ".join(tokens)
        if ("create" in low_tokens or "init" in low_tokens) and any("vite" in t for t in low_tokens):
            for i, tok in enumerate(low_tokens):
                if (tok == "create" or tok == "init") and len(tokens) > i + 2 and "vite" in low_tokens[i + 1]:
                    if not tokens[i + 2].startswith("-"):
                        tokens[i + 2] = _relativize_scaffold_target(tokens[i + 2], cwd_norm, project_root_norm)
                        return " ".join(tokens)
        return cmd_str

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

    raw_cwd = str(cwd or "").strip()
    normalized_cwd = raw_cwd if raw_cwd else "."
    if not raw_cwd and re.search(r"\bprojects/[A-Za-z0-9._-]+\b", cmd):
        normalized_cwd = "projects"
    if normalized_cwd in {"", ".", "./"}:
        normalized_cwd = project_root
    if normalized_cwd.startswith("./"):
        normalized_cwd = normalized_cwd[2:]
    normalized_cwd = _normalize_projects_path(normalized_cwd, project_root)
    cmd = _normalize_scaffold_target_arg(cmd, normalized_cwd, project_root)
    low = cmd.lower()

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
    project_name = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root

    structure_plan = _derive_structure_plan(project_root, plan)
    execution_steps: List[Dict[str, str]] = []
    for cmd in plan.get("bootstrap_commands", []):
        if isinstance(cmd, dict):
            raw_command = str(cmd.get("command", "")).strip()
            execution_steps.append(
                _normalize_execution_step(
                    project_root=project_root,
                    cwd=str(cmd.get("cwd", ".")),
                    command=raw_command,
                    purpose=str(cmd.get("purpose", "bootstrap")),
                )
            )
    workspace_context = scan_projects_root(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "projects")
    )
    snapshot_source = json.dumps(workspace_context, sort_keys=True)
    workspace_snapshot_hash = hashlib.sha1(snapshot_source.encode("utf-8")).hexdigest()

    return {
        "request_id": request_id,
        "project_root": project_root,
        "selected_project_root": project_root,
        "structure_plan": structure_plan,
        "execution_steps": execution_steps,
        "pm_checklist": plan.get("pm_checklist", {}),
        "constraints": [str(x) for x in plan.get("constraints", [])],
        "validation": [str(x) for x in plan.get("validation", [])],
        "clarifications": rounds,
        "workspace_context": workspace_context,
        "workspace_snapshot_hash": workspace_snapshot_hash,
        "pending_tasks": [],
        "internal_checklist": [],
        "checklist_cursor": "",
        "memory": {"attempted_commands": [], "errors": []},
        "dev_preflight_plan": {
            "status": "pending",
            "detected_stacks": [],
            "validation_commands": [],
            "final_compile_commands": [],
            "active_project_root": "",
        },
        "checkpoints": [],
        "task_outcomes": [],
        "path_aliases": {
            "project_root": project_root,
            "project_name": normalize_rel_path(project_name),
        },
        "command_policy": {"risk_confirmation": True, "non_interactive": True},
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
        base_path = self.store_path.replace(".json", "")
        write_handoffpack_yaml(base_path, handoff)
