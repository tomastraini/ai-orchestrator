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


class RepositoryMemoryMixin:
    @staticmethod
    def _default_repository_memory() -> Dict[str, Any]:
        return {
            "files_inspected": [],
            "symbols_discovered": [],
            "assumptions": [],
            "candidate_attempts": [],
            "candidate_rejections": [],
            "correction_attempts": [],
            "command_failures": [],
            "diagnostic_file_refs": [],
            "validation_inference": [],
            "attempted_commands": [],
            "errors": [],
            "touched_paths": [],
        }

    @staticmethod
    def _initialize_repository_memory(state: DevGraphState, handoff: Dict[str, Any] | None) -> Dict[str, Any]:
        prior_memory = handoff.get("memory", {}) if isinstance(handoff, dict) else {}
        if not bool(state.get("carry_forward_memory", True)):
            return DevMasterGraph._default_repository_memory()
        merged = DevMasterGraph._merge_repository_memory(
            DevMasterGraph._default_repository_memory(),
            prior_memory if isinstance(prior_memory, dict) else {},
        )
        if DevMasterGraph._workspace_changed_significantly(handoff):
            merged["candidate_rejections"] = []
            merged["candidate_attempts"] = []
        return DevMasterGraph._trim_repository_memory(merged)

    @staticmethod
    def _merge_repository_memory(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged: Dict[str, Any] = {
            key: (list(value) if isinstance(value, list) else [])
            for key, value in base.items()
        }
        for key, value in incoming.items():
            if key not in merged:
                merged[key] = []
            if isinstance(value, list):
                merged[key].extend(value)
        return merged

    @staticmethod
    def _workspace_changed_significantly(handoff: Dict[str, Any] | None) -> bool:
        if not isinstance(handoff, dict):
            return False
        previous = str(handoff.get("workspace_snapshot_hash", "")).strip()
        current = str(handoff.get("workspace_snapshot_hash_current", "")).strip()
        return bool(previous and current and previous != current)

    @staticmethod
    def _trim_repository_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
        for key, limit in DevMasterGraph.MEMORY_LIMITS.items():
            value = memory.get(key, [])
            if not isinstance(value, list):
                memory[key] = []
                continue
            if len(value) > int(limit):
                memory[key] = value[-int(limit) :]
        return memory

    @staticmethod
    def _ensure_repository_memory(state: DevGraphState) -> Dict[str, Any]:
        current = state.get("repository_memory")
        memory = dict(current) if isinstance(current, dict) else {}
        for key, default_value in DevMasterGraph._default_repository_memory().items():
            if key not in memory or not isinstance(memory.get(key), list):
                memory[key] = list(default_value)
        state["repository_memory"] = DevMasterGraph._trim_repository_memory(memory)
        return state["repository_memory"]

    @staticmethod
    def _remember(state: DevGraphState, bucket: str, payload: Dict[str, Any]) -> None:
        memory = DevMasterGraph._ensure_repository_memory(state)
        entry = {
            "timestamp_ms": int(time.time() * 1000),
            "phase": str(state.get("current_step", "")),
            "kind": bucket,
            "source_request_id": str(state.get("request_id", "")),
            "iteration_index": int(state.get("iteration_index", 0) or 0),
            "data": payload,
        }
        memory.setdefault(bucket, []).append(entry)
        state["repository_memory"] = DevMasterGraph._trim_repository_memory(memory)

    @staticmethod
    def _remember_text_value(state: DevGraphState, bucket: str, value: str) -> None:
        memory = DevMasterGraph._ensure_repository_memory(state)
        items = memory.setdefault(bucket, [])
        if value and value not in items:
            items.append(value)
        state["repository_memory"] = DevMasterGraph._trim_repository_memory(memory)

    @staticmethod
    def _record_error_file_refs(state: DevGraphState, refs: List[str]) -> None:
        for ref in refs:
            normalized = str(ref or "").replace("\\", "/").strip()
            if not normalized:
                continue
            DevMasterGraph._remember_text_value(state, "diagnostic_file_refs", normalized)

    @staticmethod
    def _has_recent_candidate_rejection(state: DevGraphState, candidate_path: str) -> bool:
        normalized = str(candidate_path or "").replace("\\", "/").strip().casefold()
        memory = DevMasterGraph._ensure_repository_memory(state)
        rejections = memory.get("candidate_rejections", [])
        if not isinstance(rejections, list):
            return False
        for entry in reversed(rejections):
            data = entry.get("data", {}) if isinstance(entry, dict) else {}
            candidate = str(data.get("candidate_path", "")).replace("\\", "/").strip().casefold()
            if candidate and candidate == normalized:
                return True
        return False
