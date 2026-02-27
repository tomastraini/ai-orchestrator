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


class FinalizeResultHandlerMixin:
    @staticmethod
    def _finalize_result_impl(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "finalize_result"
        DevMasterGraph._emit(state, "[PHASE_START] finalize_result")
        memory = DevMasterGraph._ensure_repository_memory(state)
        for path in [str(x) for x in state.get("touched_paths", []) if isinstance(x, str)]:
            DevMasterGraph._remember_text_value(state, "touched_paths", DevMasterGraph._relpath_safe(state, path))
        for err in [str(x) for x in state.get("errors", []) if isinstance(x, str)]:
            DevMasterGraph._remember_text_value(state, "errors", DevMasterGraph._sanitize_text(err, 320))
        for outcome in state.get("task_outcomes", []):
            if not isinstance(outcome, dict):
                continue
            cmd = str(outcome.get("command", "")).strip()
            if cmd:
                DevMasterGraph._remember_text_value(state, "attempted_commands", cmd)
        memory = DevMasterGraph._ensure_repository_memory(state)
        if state.get("bootstrap_status") == "failed":
            state["status"] = "bootstrap_failed"
        elif state.get("implementation_status") == "impl_skipped":
            state["status"] = "recoverable_blocked"
        elif state.get("validation_status") not in {"completed", "skipped"}:
            state["status"] = "recoverable_blocked"
        elif state.get("final_compile_status") != "completed":
            state["status"] = "partial_progress"
        elif not DevMasterGraph._all_mandatory_checklist_items_completed(state):
            state["status"] = "partial_progress"
            state["errors"].append("[CHECKLIST] mandatory items remain incomplete.")
        else:
            state["status"] = "completed"

        terminal_gate = DevMasterGraph._terminal_failure_gate(state)
        DevMasterGraph._emit_event(
            state,
            "terminal_failure_gate_entered",
            status_before=state.get("status", ""),
            gate=terminal_gate,
        )
        if state.get("status") not in {"completed", "bootstrap_failed"}:
            if bool(terminal_gate.get("approved", False)):
                state["status"] = "implementation_failed"
                DevMasterGraph._emit_event(
                    state,
                    "terminal_failure_gate_approved",
                    criterion=terminal_gate.get("criterion", "none"),
                    gate=terminal_gate,
                )
            else:
                DevMasterGraph._emit_event(
                    state,
                    "terminal_failure_gate_rejected",
                    criterion=terminal_gate.get("criterion", "none"),
                    gate=terminal_gate,
                )
        status = str(state.get("status", "unknown"))
        continuation_reason_map = {
            "completed": "completed_with_followup_possible",
            "partial_progress": "partial_progress_continue_recommended",
            "recoverable_blocked": "recoverable_blocker_continue_recommended",
            "bootstrap_failed": "bootstrap_failed_continue_possible",
            "implementation_failed": "terminal_failure_gate_approved",
        }
        continuation_eligible = status in {
            "completed",
            "partial_progress",
            "recoverable_blocked",
            "bootstrap_failed",
        }
        needs_validation_clarification = bool(state.get("needs_validation_clarification", False))
        continuation_guidance = {
            "status": status,
            "continuation_reason": continuation_reason_map.get(status, "continuation_not_available"),
            "needs_validation_clarification": needs_validation_clarification,
            "followup_options": (
                [x for x in state.get("validation_followup_options", []) if isinstance(x, dict)]
                if isinstance(state.get("validation_followup_options"), list)
                else []
            ),
            "recommended_next_step": (
                "Clarify validation approach and continue iterative improvement."
                if needs_validation_clarification
                else "Provide the next improvement requirement."
            ),
        }
        state["continuation_eligible"] = continuation_eligible
        state["ready_for_followup"] = continuation_eligible
        state["continuation_reason"] = continuation_reason_map.get(status, "continuation_not_available")
        state["continuation_guidance"] = continuation_guidance
        DevMasterGraph._emit_event(
            state,
            "continuation_offered" if continuation_eligible else "continuation_blocked",
            status=status,
            continuation_eligible=continuation_eligible,
            continuation_reason=state.get("continuation_reason", ""),
        )
        DevMasterGraph._emit_event(
            state,
            "continuation_guidance_ready",
            guidance=continuation_guidance,
        )
        err_count = len(state.get("errors", []))
        checklist_total = len(state.get("internal_checklist", []))
        checklist_completed = len(
            [
                item
                for item in state.get("internal_checklist", [])
                if isinstance(item, dict) and str(item.get("status", "")) == "completed"
            ]
        )
        state["pending_tasks"] = [
            str(item.get("id", "")).strip()
            for item in state.get("internal_checklist", [])
            if isinstance(item, dict) and str(item.get("status", "")) != "completed"
        ]
        state["final_summary"] = (
            f"Developer master finished with status={state['status']} and errors={err_count}. "
            f"phase_status={state.get('phase_status', {})} "
            f"pass_status={state.get('implementation_pass_statuses', [])} "
            f"checklist={checklist_completed}/{checklist_total} "
            f"ready_for_followup={state.get('ready_for_followup', False)} "
            f"continuation_reason={state.get('continuation_reason', '')} "
            f"needs_validation_clarification={bool(state.get('needs_validation_clarification', False))}"
        )
        outcomes = [x for x in state.get("task_outcomes", []) if isinstance(x, dict)]
        failed_outcomes = [x for x in outcomes if str(x.get("status", "")) != "completed"]
        candidate_attempts = memory.get("candidate_attempts", []) if isinstance(memory.get("candidate_attempts"), list) else []
        candidate_rejections = memory.get("candidate_rejections", []) if isinstance(memory.get("candidate_rejections"), list) else []
        validation_inference = memory.get("validation_inference", []) if isinstance(memory.get("validation_inference"), list) else []
        rejected_paths = {
            str(item.get("data", {}).get("candidate_path", "")).replace("\\", "/").strip().casefold()
            for item in candidate_rejections
            if isinstance(item, dict) and isinstance(item.get("data"), dict)
        }
        repeated_retry_count = 0
        total_attempt_count = 0
        for item in candidate_attempts:
            if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
                continue
            total_attempt_count += 1
            path = str(item.get("data", {}).get("candidate_path", "")).replace("\\", "/").strip().casefold()
            if path and path in rejected_paths:
                repeated_retry_count += 1
        bootstrap_outcomes = [
            o
            for o in outcomes
            if str(o.get("task_id", "")).startswith("bootstrap_") or str(o.get("task_id", "")).startswith("handoff_")
        ]
        reliability_metrics = {
            "target_selection_precision": (
                max(0.0, 1.0 - (len(candidate_rejections) / max(1, len(candidate_attempts))))
                if candidate_attempts
                else 1.0
            ),
            "repeated_failed_candidate_retry_rate": repeated_retry_count / max(1, total_attempt_count),
            "validation_executability_rate": (
                1.0
                if not validation_inference
                else (
                    sum(
                        1
                        for item in validation_inference
                        if isinstance(item, dict)
                        and isinstance(item.get("data"), dict)
                        and item.get("data", {}).get("classification") != "non_executable"
                    )
                    / max(1, len(validation_inference))
                )
            ),
            "compile_gate_execution_rate": 1.0 if state.get("final_compile_status") in {"completed", "failed"} else 0.0,
            "autonomous_recovery_success_rate": (
                1.0
                if any(str(x).startswith("final_compile_recovery_") for x in [o.get("task_id", "") for o in outcomes])
                and state.get("final_compile_status") == "completed"
                else 0.0
            ),
            "implementation_failed": 1.0 if state.get("status") == "implementation_failed" else 0.0,
            "task_failure_rate": len(failed_outcomes) / max(1, len(outcomes)),
            "bootstrap_true_success_rate": (
                sum(1 for o in bootstrap_outcomes if str(o.get("status", "")) == "completed") / max(1, len(bootstrap_outcomes))
                if bootstrap_outcomes
                else 1.0
            ),
            "semantic_recovery_acceptance_rate": (
                sum(
                    1
                    for event in state.get("telemetry_events", [])
                    if isinstance(event, dict) and str(event.get("category", "")) == "final_compile_recovery_started"
                )
                / max(
                    1,
                    sum(
                        1
                        for event in state.get("telemetry_events", [])
                        if isinstance(event, dict)
                        and str(event.get("category", "")) in {"recovery_intent_mismatch", "final_compile_recovery_started"}
                    ),
                )
            ),
            "implementation_cascade_failure_rate": (
                sum(
                    1
                    for item in state.get("internal_checklist", [])
                    if isinstance(item, dict)
                    and str(item.get("kind", "")) == "implementation"
                    and str(item.get("status", "")) == "failed"
                )
                / max(
                    1,
                    sum(
                        1
                        for item in state.get("internal_checklist", [])
                        if isinstance(item, dict) and str(item.get("kind", "")) == "implementation"
                    ),
                )
            ),
        }
        state["reliability_metrics"] = reliability_metrics
        DevMasterGraph._emit_event(
            state,
            "final_summary",
            status=state.get("status", "unknown"),
            errors=err_count,
            checklist_total=checklist_total,
            checklist_completed=checklist_completed,
            phase_status=state.get("phase_status", {}),
            implementation_passes=state.get("implementation_pass_statuses", []),
            task_outcomes=len(state.get("task_outcomes", [])),
            reliability_metrics=reliability_metrics,
        )
        DevMasterGraph._emit(state, f"[FINAL] {state['final_summary']}")
        DevMasterGraph._persist_run_artifacts(state)
        state["phase_status"]["finalize_result"] = "completed"
        return state
