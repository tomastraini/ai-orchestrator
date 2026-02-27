from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from langgraph.graph import END, START, StateGraph

from services.dev.dev_executor import execute_dev_tasks, execute_single_recovery_command
from services.dev.edit_validator import validate_intent_alignment
from services.dev.phases.ask_cli_clarifications import run as ask_cli_clarifications_phase
from services.dev.phases.derive_dev_todos import run as derive_dev_todos_phase
from services.dev.phases.dev_preflight_planning import run as dev_preflight_planning_phase
from services.dev.phases.execute_bootstrap_phase import run as execute_bootstrap_phase
from services.dev.phases.execute_final_compile_gate import run as execute_final_compile_gate
from services.dev.phases.execute_implementation_phase import run as execute_implementation_phase
from services.dev.phases.execute_implementation_target import run as execute_implementation_target
from services.dev.phases.execute_validation_phase import run as execute_validation_phase
from services.dev.phases.finalize_result import run as finalize_result_phase
from services.dev.phases.ingest_pm_plan import run as ingest_pm_plan_phase
from services.dev.phases.prepare_execution_steps import run as prepare_execution_steps_phase
from services.dev.edit_primitives import patch_region, rename_path
from services.dev.types.dev_graph_state import DevGraphState
from services.workspace.cognition.scaffold_probe import probe_scaffold_layout
from services.workspace.cognition.snapshot_store import persist_cognition_snapshot
from services.workspace.project_index import build_cognition_index, detect_stack_from_markers, rank_candidate_files, scan_workspace_context
from shared.dev_schemas import DevChecklistItem, DevTask, derive_project_name
from shared.pathing import canonicalize_scope_path


class ProjectIndexManagerMixin:
    @staticmethod
    def _build_dev_technical_plan(state: DevGraphState) -> Dict[str, Any]:
        targets = state.get("implementation_targets", [])
        steps = state.get("bootstrap_tasks", [])
        validations = state.get("validation_tasks", [])
        affected_files: List[Dict[str, Any]] = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            affected_files.append(
                {
                    "path_hint": str(target.get("expected_path_hint", "")),
                    "file_name": str(target.get("file_name", "")),
                    "change_type": str(target.get("modification_type", "modify")),
                    "creation_policy": str(target.get("creation_policy", "")),
                    "rationale": str(target.get("details", "")),
                }
            )
        todos: List[Dict[str, Any]] = []
        for idx, target in enumerate(affected_files, start=1):
            todos.append(
                {
                    "id": f"dev_todo_{idx}",
                    "description": f"{target['change_type']} {target['path_hint'] or target['file_name']}".strip(),
                    "acceptance_criteria": [
                        "File change is applied and syntactically valid",
                        "Change aligns with PM acceptance criteria and constraints",
                    ],
                }
            )
        command_plan = [
            {"cwd": str(task.cwd or "."), "command": str(task.command or ""), "purpose": str(task.description)}
            for task in steps
            if isinstance(task, DevTask)
        ]
        validation_plan = [
            {"id": str(task.id), "description": str(task.description), "command": str(task.command or "")}
            for task in validations
            if isinstance(task, DevTask)
        ]
        technical_plan = {
            "project_root": str(state.get("project_root", "")),
            "affected_files": affected_files,
            "command_plan": command_plan,
            "todo_plan": todos,
            "validation_plan": validation_plan,
            "discovery_candidates": state.get("dev_discovery_candidates", [])[:20],
        }
        state["dev_technical_plan"] = technical_plan
        DevMasterGraph._emit_event(
            state,
            "dev_technical_plan_built",
            affected_files=affected_files,
            command_count=len(command_plan),
            todo_count=len(todos),
            validation_count=len(validation_plan),
        )
        return technical_plan

    @staticmethod
    def _build_llm_context_contract(state: DevGraphState) -> Dict[str, Any]:
        scope_root = str(state.get("scope_root", "")).strip()
        resolved_root = str(state.get("active_project_root", "")).strip()
        project_name = str(state.get("project_name", "")).strip()
        root_evidence = state.get("root_resolution_evidence", {}) if isinstance(state.get("root_resolution_evidence"), dict) else {}
        normalized_targets: List[Dict[str, str]] = []
        for target in state.get("implementation_targets", []):
            if not isinstance(target, dict):
                continue
            expected = str(target.get("expected_path_hint", ""))
            file_name = str(target.get("file_name", ""))
            creation_policy = str(target.get("creation_policy", ""))
            resolved = ""
            try:
                resolved = DevMasterGraph._resolve_target_file_path(
                    scope_root=scope_root,
                    project_root=str(state.get("project_root", "")),
                    active_project_root=resolved_root,
                    expected_path_hint=expected,
                    file_name=file_name,
                )
            except Exception:
                resolved = ""
            normalized_targets.append(
                {
                    "expected_path_hint": expected,
                    "file_name": file_name,
                    "creation_policy": creation_policy,
                    "resolved_absolute_path": resolved,
                }
            )

        tree_snapshot: List[str] = []
        if resolved_root and os.path.isdir(resolved_root):
            try:
                entries = sorted(os.listdir(resolved_root))[:30]
                tree_snapshot = entries
            except Exception:
                tree_snapshot = []

        return {
            "scope_root": scope_root,
            "resolved_active_root": resolved_root,
            "project_name": project_name,
            "candidate_roots": root_evidence.get("candidates", []),
            "root_confidence": root_evidence.get("confidence", 0),
            "path_aliases": {
                "project_root": str(state.get("project_root", "")),
                "active_project_root": resolved_root,
            },
            "normalized_targets": normalized_targets,
            "active_root_tree_snapshot": tree_snapshot,
        }

    @staticmethod
    def _build_active_root_file_index(active_root: str) -> Dict[str, Any]:
        files: List[str] = []
        by_basename: Dict[str, List[str]] = {}
        by_basename_casefold: Dict[str, List[str]] = {}
        by_suffix_casefold: Dict[str, str] = {}
        if not active_root or not os.path.isdir(active_root):
            return {
                "active_root": active_root,
                "files": files,
                "by_basename": by_basename,
                "by_basename_casefold": by_basename_casefold,
                "by_suffix_casefold": by_suffix_casefold,
                "cognition": {
                    "version": "2.0",
                    "active_root": active_root,
                    "file_count": 0,
                    "symbol_index": {"files": [], "by_name": {}},
                    "entrypoints": [],
                    "entrypoint_aliases": {},
                    "resolution_hints": {},
                    "provider_capabilities": {},
                },
            }
        for root, dirs, names in os.walk(active_root):
            dirs[:] = [d for d in dirs if d not in DevMasterGraph.INDEX_IGNORE_DIRS]
            for name in names:
                abs_path = os.path.join(root, name)
                rel = os.path.relpath(abs_path, active_root).replace("\\", "/")
                files.append(rel)
                base = os.path.basename(rel)
                by_basename.setdefault(base, []).append(rel)
                by_basename_casefold.setdefault(base.casefold(), []).append(rel)
                suffixes = rel.split("/")
                for idx in range(len(suffixes)):
                    suffix = "/".join(suffixes[idx:]).casefold()
                    if suffix and suffix not in by_suffix_casefold:
                        by_suffix_casefold[suffix] = rel
        cognition = build_cognition_index(active_root, files)
        scaffold_probe = probe_scaffold_layout(active_root, limit=1200)
        return {
            "active_root": active_root,
            "files": files,
            "by_basename": by_basename,
            "by_basename_casefold": by_basename_casefold,
            "by_suffix_casefold": by_suffix_casefold,
            "scaffold_probe": scaffold_probe,
            "cognition": cognition,
        }

    @staticmethod
    def _emit_index_snapshot(state: DevGraphState, index: Dict[str, Any], category: str) -> None:
        files = index.get("files", []) if isinstance(index.get("files"), list) else []
        preview = sorted(files)[:30]
        DevMasterGraph._emit_event(
            state,
            category,
            active_root=DevMasterGraph._relpath_safe(state, str(index.get("active_root", ""))),
            file_count=len(files),
            top_entries=preview,
        )

    @staticmethod
    def _refresh_active_root_index(state: DevGraphState, *, category: str) -> Dict[str, Any]:
        active_root = str(state.get("active_project_root", "")).strip()
        scope_root = str(state.get("scope_root", "")).strip()
        if active_root and scope_root:
            active_root = canonicalize_scope_path(scope_root, active_root)
        if not active_root:
            project_root = str(state.get("project_root", "")).strip()
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            active_root = os.path.join(scope_root, rel) if scope_root else active_root
        if active_root and scope_root:
            active_root = canonicalize_scope_path(scope_root, active_root)
            state["active_project_root"] = active_root
        index = DevMasterGraph._build_active_root_file_index(active_root)
        state["active_root_file_index"] = index
        DevMasterGraph._emit_index_snapshot(state, index, category)
        probe = index.get("scaffold_probe", {}) if isinstance(index.get("scaffold_probe"), dict) else {}
        if probe:
            files = probe.get("files", []) if isinstance(probe.get("files"), list) else []
            top_level = probe.get("top_level", []) if isinstance(probe.get("top_level"), list) else []
            DevMasterGraph._emit_event(
                state,
                "scaffold_probe_snapshot",
                phase=category,
                file_count=len(files),
                top_level=top_level[:30],
            )
        cognition = index.get("cognition", {}) if isinstance(index.get("cognition"), dict) else {}
        providers = cognition.get("provider_capabilities", {}) if isinstance(cognition, dict) else {}
        if providers:
            DevMasterGraph._emit_event(state, "cognition_provider_capabilities", **providers)
        request_id = str(state.get("request_id", "")).strip()
        project_name = str(state.get("project_name", "")).strip() or derive_project_name(state.get("plan", {}))
        if request_id and project_name and str(state.get("scope_root", "")).strip():
            snapshot_path = persist_cognition_snapshot(
                repo_root=str(state.get("scope_root", "")).strip(),
                project_name=project_name,
                run_id=request_id,
                phase=category,
                cognition_index=cognition if isinstance(cognition, dict) else {},
            )
            if snapshot_path:
                DevMasterGraph._emit_event(
                    state,
                    "cognition_snapshot_created",
                    phase=category,
                    snapshot_path=DevMasterGraph._relpath_safe(state, snapshot_path),
                )
        return index
