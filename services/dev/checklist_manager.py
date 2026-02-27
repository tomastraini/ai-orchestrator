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


class ChecklistManagerMixin:
    @staticmethod
    def _reindex_checklist(state: DevGraphState) -> None:
        checklist = state.get("internal_checklist", [])
        state["checklist_index"] = {
            str(item.get("id", "")): idx
            for idx, item in enumerate(checklist)
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }

    @staticmethod
    def _upsert_checklist_item(state: DevGraphState, item: DevChecklistItem) -> None:
        checklist = state.get("internal_checklist", [])
        index = state.get("checklist_index", {})
        payload = asdict(item)
        item_id = item.id
        if item_id in index:
            checklist[index[item_id]] = payload
        else:
            checklist.append(payload)
        state["internal_checklist"] = checklist
        DevMasterGraph._reindex_checklist(state)

    @staticmethod
    def _find_checklist_item(state: DevGraphState, item_id: str) -> Optional[Dict[str, Any]]:
        idx = state.get("checklist_index", {}).get(item_id)
        if idx is None:
            return None
        checklist = state.get("internal_checklist", [])
        if idx < 0 or idx >= len(checklist):
            return None
        item = checklist[idx]
        return item if isinstance(item, dict) else None

    @staticmethod
    def _append_item_evidence(item: Dict[str, Any], evidence: Optional[Dict[str, Any]]) -> None:
        if not evidence:
            return
        current = item.get("evidence")
        if not isinstance(current, list):
            current = []
        current.append(evidence)
        item["evidence"] = current

    @staticmethod
    def _set_checklist_status(
        state: DevGraphState,
        item_id: str,
        status: str,
        *,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        item = DevMasterGraph._find_checklist_item(state, item_id)
        if not item:
            return
        item["status"] = status
        DevMasterGraph._append_item_evidence(item, evidence)
        state["checklist_cursor"] = item_id
        DevMasterGraph._emit(
            state,
            f"[CHECKLIST] item={item_id} status={status}",
        )
        DevMasterGraph._emit_event(
            state,
            "checklist_outcome",
            item_id=item_id,
            status=status,
            evidence=evidence or {},
        )

    @staticmethod
    def _next_actionable_checklist_item(state: DevGraphState) -> Optional[Dict[str, Any]]:
        checklist = state.get("internal_checklist", [])
        completed = {
            str(item.get("id", ""))
            for item in checklist
            if isinstance(item, dict) and str(item.get("status", "")) == "completed"
        }
        for item in checklist:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "pending"))
            if status in {"completed", "failed"}:
                continue
            deps = item.get("depends_on", [])
            if isinstance(deps, list) and any(str(dep) not in completed for dep in deps):
                continue
            return item
        return None

    @staticmethod
    def _all_mandatory_checklist_items_completed(state: DevGraphState) -> bool:
        for item in state.get("internal_checklist", []):
            if not isinstance(item, dict):
                continue
            if not bool(item.get("mandatory", True)):
                continue
            if str(item.get("status", "")) != "completed":
                return False
        return True

    @staticmethod
    def _build_internal_checklist(state: DevGraphState) -> None:
        handoff = state.get("handoff") or {}
        restored = handoff.get("internal_checklist")
        reopened_ids = {
            str(x).strip()
            for x in (handoff.get("reopened_checklist_ids", []) if isinstance(handoff, dict) else [])
            if str(x).strip()
        }
        restored_by_id: Dict[str, Dict[str, Any]] = {}
        if isinstance(restored, list) and restored:
            for item in restored:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id", "")).strip()
                if not item_id:
                    continue
                restored_by_id[item_id] = item

        checklist: List[Dict[str, Any]] = []
        for task in state.get("bootstrap_tasks", []):
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="bootstrap",
                    description=task.description,
                    task_ref=task.id,
                    success_criteria=["command exits with code 0"],
                )
            )
            checklist.append(DevMasterGraph._reconcile_checklist_item(restored_by_id.get(item_id), default_item, reopened_ids))
        for idx, target in enumerate(state.get("implementation_targets", []), start=1):
            file_name = str(target.get("file_name", "")).strip() or f"target_{idx}"
            item_id = f"todo_impl_{idx}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="implementation",
                    description=f"implement {file_name}",
                    target_ref=str(target.get("expected_path_hint", file_name)),
                    success_criteria=["target file mutated with evidence"],
                )
            )
            checklist.append(DevMasterGraph._reconcile_checklist_item(restored_by_id.get(item_id), default_item, reopened_ids))
        for task in state.get("validation_tasks", []):
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="validation",
                    description=task.description,
                    task_ref=task.id,
                    success_criteria=["validation task completed"],
                    mandatory=False,
                )
            )
            checklist.append(DevMasterGraph._reconcile_checklist_item(restored_by_id.get(item_id), default_item, reopened_ids))
        for task in state.get("final_compile_tasks", []):
            deps = [str(item.get("id")) for item in checklist if isinstance(item, dict)]
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="final_compile",
                    description=task.description,
                    task_ref=task.id,
                    depends_on=deps,
                    success_criteria=["compile/build gate command completed"],
                )
            )
            checklist.append(DevMasterGraph._reconcile_checklist_item(restored_by_id.get(item_id), default_item, reopened_ids))
        delta_requirement = str(state.get("delta_requirement", "")).strip()
        if delta_requirement:
            delta_item_id = f"todo_delta_{int(state.get('iteration_index', 0) or 0)}"
            default_delta_item = asdict(
                DevChecklistItem(
                    id=delta_item_id,
                    kind="validation",
                    description=f"validate delta requirement: {delta_requirement}",
                    success_criteria=["delta requirement addressed in implementation or explanation"],
                    mandatory=False,
                )
            )
            checklist.append(
                DevMasterGraph._reconcile_checklist_item(
                    restored_by_id.get(delta_item_id),
                    default_delta_item,
                    reopened_ids,
                )
            )
        state["internal_checklist"] = checklist
        DevMasterGraph._reindex_checklist(state)
        if restored_by_id:
            DevMasterGraph._emit(
                state,
                f"[CHECKLIST] restored_and_reconciled items={len(checklist)} restored={len(restored_by_id)}",
            )
            if reopened_ids:
                DevMasterGraph._emit_event(
                    state,
                    "checklist_reopened",
                    reopened_count=len(reopened_ids),
                    reopened_ids=sorted(reopened_ids),
                )
        else:
            DevMasterGraph._emit(state, f"[CHECKLIST] initialized items={len(checklist)}")

    @staticmethod
    def _reconcile_checklist_item(
        restored: Dict[str, Any] | None,
        default_item: Dict[str, Any],
        reopened_ids: Set[str],
    ) -> Dict[str, Any]:
        if not isinstance(restored, dict):
            return default_item
        merged = dict(default_item)
        merged.update(restored)
        item_id = str(merged.get("id", "")).strip()
        if item_id and item_id in reopened_ids:
            merged["status"] = "reopened_by_delta"
            merged["status_reason"] = "reopened_by_delta"
        else:
            status = str(merged.get("status", "pending")).strip() or "pending"
            if status not in {"completed", "pending", "failed", "blocked", "reopened_by_delta"}:
                status = "pending"
            merged["status"] = status
            if "status_reason" not in merged:
                merged["status_reason"] = status
        return merged
