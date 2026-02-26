from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List

from services.pm.handoffpack_writer import write_handoffpack_yaml
from services.workspace.project_index import scan_projects_root
from services.workspace.cognition.index_builder import build_cognition_index
from services.workspace.cognition.scaffold_probe import probe_scaffold_layout
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


def _extract_target_file_metadata(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    targets = plan.get("target_files") if isinstance(plan.get("target_files"), list) else []
    metadata: List[Dict[str, Any]] = []
    for item in targets:
        if not isinstance(item, dict):
            continue
        metadata.append(
            {
                "file_name": str(item.get("file_name", "")).strip(),
                "expected_path_hint": str(item.get("expected_path_hint", "")).strip(),
                "creation_policy": str(item.get("creation_policy", "")).strip(),
                "symbol_hints": [str(x) for x in item.get("symbol_hints", []) if isinstance(x, str)],
                "candidate_paths": item.get("candidate_paths", []) if isinstance(item.get("candidate_paths"), list) else [],
                "path_confidence": item.get("path_confidence"),
                "entrypoint_candidate": bool(item.get("entrypoint_candidate", False)),
            }
        )
    return metadata


def _build_cognition_snapshot(repo_root: str, project_root: str) -> Dict[str, Any]:
    rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
    active_root = os.path.join(repo_root, rel)
    if not os.path.isdir(active_root):
        return {}
    probe = probe_scaffold_layout(active_root, limit=1200)
    rel_files = probe.get("files", []) if isinstance(probe, dict) else []
    if not isinstance(rel_files, list):
        rel_files = []
    return build_cognition_index(active_root, [str(x) for x in rel_files])


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

    raw_cwd = str(cwd or "").strip()
    normalized_cwd = raw_cwd if raw_cwd else "."
    if normalized_cwd in {"", ".", "./"}:
        normalized_cwd = project_root
    if normalized_cwd.startswith("./"):
        normalized_cwd = normalized_cwd[2:]
    normalized_cwd = _normalize_projects_path(normalized_cwd, project_root)

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
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    workspace_context = scan_projects_root(os.path.join(repo_root, "projects"))
    target_file_metadata = _extract_target_file_metadata(plan)
    cognition_snapshot = _build_cognition_snapshot(repo_root, project_root)
    snapshot_source = json.dumps(workspace_context, sort_keys=True)
    workspace_snapshot_hash = hashlib.sha1(snapshot_source.encode("utf-8")).hexdigest()

    return {
        "request_id": request_id,
        "project_root": project_root,
        "selected_project_root": project_root,
        "structure_plan": structure_plan,
        "execution_steps": execution_steps,
        "execution_origin": "pm",
        "target_intents": [
            dict(intent)
            for intent in plan.get("target_intents", [])
            if isinstance(intent, dict)
        ],
        "repo_structure_snapshot": (
            dict(plan.get("repo_structure_snapshot", {}))
            if isinstance(plan.get("repo_structure_snapshot"), dict)
            else {}
        ),
        "pm_checklist": plan.get("pm_checklist", {}),
        "constraints": [str(x) for x in plan.get("constraints", [])],
        "validation": [str(x) for x in plan.get("validation", [])],
        "clarifications": rounds,
        "workspace_context": workspace_context,
        "cognition_snapshot": cognition_snapshot,
        "target_file_metadata": target_file_metadata,
        "workspace_snapshot_hash": workspace_snapshot_hash,
        "pending_tasks": [],
        "internal_checklist": [],
        "checklist_cursor": "",
        "checklist_status_reason_codes": [
            "completed",
            "pending",
            "failed",
            "blocked",
            "reopened_by_delta",
        ],
        "memory": {"attempted_commands": [], "errors": []},
        "continuation": {
            "session_id": "",
            "parent_request_id": "",
            "iteration_index": 0,
            "continuation_reason": "initial",
            "delta_requirement": "",
            "prior_run_summary": "",
            "carry_forward_memory": True,
            "trigger_type": "initial",
            "continuation_mode": "always",
            "continuation_guidance": {},
        },
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
