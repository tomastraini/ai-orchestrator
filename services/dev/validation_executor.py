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


class ValidationExecutorMixin:
    @staticmethod
    def _execute_validation_phase_impl(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_validation_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_validation_phase")
        DevMasterGraph._ensure_repository_memory(state)
        if state.get("bootstrap_status") == "failed" or state.get("implementation_status") == "failed":
            state["validation_status"] = "skipped"
            state["phase_status"]["execute_validation_phase"] = "skipped"
            DevMasterGraph._emit(state, "[VALIDATION] skipped due to previous failure")
            return state

        preflight = state.get("dev_preflight_plan", {}) if isinstance(state.get("dev_preflight_plan"), dict) else {}
        raw_requirements = preflight.get("raw_validation_requirements", [])
        unresolved_requirements = preflight.get("unresolved_validation_requirements", [])
        validation_strategy = (
            preflight.get("validation_strategy", {})
            if isinstance(preflight.get("validation_strategy"), dict)
            else {}
        )
        followup_options = (
            validation_strategy.get("followup_options", [])
            if isinstance(validation_strategy.get("followup_options"), list)
            else []
        )
        state["validation_followup_options"] = [x for x in followup_options if isinstance(x, dict)]
        unresolved_text = (
            [str(item.get("requirement", "")) for item in unresolved_requirements if isinstance(item, dict)]
            if isinstance(unresolved_requirements, list)
            else []
        )
        validation_tasks = state.get("validation_tasks", [])

        if raw_requirements and unresolved_requirements and not validation_tasks:
            msg = (
                "[VALIDATION] required validations were provided by PM but none were executable: "
                f"{unresolved_text or unresolved_requirements}"
            )
            browser_adapter = state.get("browser_validation_adapter")
            if callable(browser_adapter):
                try:
                    adapter_result = browser_adapter(
                        {
                            "request_id": state.get("request_id", ""),
                            "active_project_root": state.get("active_project_root", ""),
                            "raw_requirements": raw_requirements,
                            "unresolved_requirements": unresolved_requirements,
                        }
                    )
                except Exception as exc:
                    adapter_result = {"status": "failed", "error": str(exc)}
                if isinstance(adapter_result, dict) and str(adapter_result.get("status", "")).strip() == "completed":
                    state["validation_status"] = "completed"
                    state["phase_status"]["execute_validation_phase"] = "completed"
                    state["needs_validation_clarification"] = False
                    state["validation_evidence"].append(
                        {
                            "strategy": "browser_adapter",
                            "notes": str(adapter_result.get("notes", "browser adapter validation completed")).strip(),
                            "steps": [
                                str(x)
                                for x in adapter_result.get("steps", [])
                                if isinstance(x, str) and str(x).strip()
                            ],
                            "observations": [
                                str(x)
                                for x in adapter_result.get("observations", [])
                                if isinstance(x, str) and str(x).strip()
                            ],
                        }
                    )
                    DevMasterGraph._emit_event(
                        state,
                        "validation_browser_adapter_completed",
                        evidence=adapter_result,
                    )
                    DevMasterGraph._emit(state, "[VALIDATION] completed via browser adapter")
                    return state
            state["needs_validation_clarification"] = True
            for item in state.get("internal_checklist", []):
                if isinstance(item, dict) and str(item.get("kind")) == "validation":
                    item["status"] = "blocked"
                    DevMasterGraph._append_item_evidence(
                        item,
                        {"phase": "validation", "warning": msg, "non_executable_requirements": unresolved_requirements},
                    )
            state["validation_status"] = "skipped"
            state["phase_status"]["execute_validation_phase"] = "skipped"
            DevMasterGraph._remember(
                state,
                "validation_inference",
                {
                    "classification": "non_executable",
                    "raw_requirements": raw_requirements,
                    "unresolved_requirements": unresolved_requirements,
                    "fallback_policy": "compile_gate_must_still_run",
                },
            )
            DevMasterGraph._emit_event(
                state,
                "validation_skipped_non_executable",
                unresolved_requirements=unresolved_requirements,
                raw_requirements=raw_requirements,
                followup_options=state.get("validation_followup_options", []),
            )
            DevMasterGraph._emit_event(
                state,
                "validation_clarification_required",
                reason="non_executable_requirements",
                followup_options=state.get("validation_followup_options", []),
            )
            DevMasterGraph._emit(state, msg)
            return state

        filtered_validation_tasks: List[DevTask] = []
        for task in validation_tasks:
            item = DevMasterGraph._find_checklist_item(state, f"todo_{task.id}")
            if item and str(item.get("status", "")) == "completed":
                continue
            filtered_validation_tasks.append(task)

        if not filtered_validation_tasks:
            state["validation_status"] = "completed"
            state["phase_status"]["execute_validation_phase"] = "completed"
            DevMasterGraph._emit(state, "[VALIDATION] no pending executable validations; marked completed")
            return state

        active_root = str(state.get("active_project_root", "")).strip()
        if active_root:
            filtered_validation_tasks = [
                DevTask(
                    id=task.id,
                    description=task.description,
                    command=task.command,
                    cwd=active_root,
                    kind=task.kind,
                )
                for task in filtered_validation_tasks
            ]
            DevMasterGraph._emit(state, f"[VALIDATION] reconciled task cwd to active root {active_root}")
        state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
        for task in filtered_validation_tasks:
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{task.id}",
                "in_progress",
                evidence={"phase": "validation", "task_id": task.id},
            )

        logs, touched_paths, errors, attempt_history, pending, outcomes = execute_dev_tasks(
            filtered_validation_tasks,
            scope_root=state["scope_root"],
            max_retries=int(state.get("max_retries", 5)),
            reserve_last_for_llm=False,
            log_sink=state.get("log_sink"),
            ask_confirmation=(lambda q: bool(state.get("ask_user")(q).strip().lower() in {"y", "yes", "true", "1"})) if callable(state.get("ask_user")) else None,
            stack_hint=(state.get("detected_stacks") or ["generic"])[0],
            ask_runtime_prompt=(lambda q: state.get("ask_user")(f"[DEV RUNTIME PROMPT] {q} (y/N)")) if callable(state.get("ask_user")) else None,
            interactive_prompt_timeout_seconds=60.0,
            constraints=[
                str(x).strip()
                for x in state.get("plan", {}).get("constraints", [])
                if isinstance(x, str) and str(x).strip()
            ],
            command_run_mode="auto",
            event_sink=(lambda event: DevMasterGraph._emit_event(state, "executor_event", **event)),
        )
        state["logs"].extend(logs)
        state["touched_paths"].extend(touched_paths)
        state["attempt_history"].extend(attempt_history)
        state["task_outcomes"].extend(outcomes)
        for outcome in outcomes:
            if str(outcome.get("status", "")) != "completed":
                DevMasterGraph._remember(
                    state,
                    "command_failures",
                    {
                        "phase": "validation",
                        "task_id": outcome.get("task_id"),
                        "category": outcome.get("category", "unknown"),
                        "exit_code": outcome.get("exit_code"),
                        "stdout_excerpt": outcome.get("stdout_excerpt", ""),
                        "stderr_excerpt": outcome.get("stderr_excerpt", ""),
                    },
                )
        error_file_refs = DevMasterGraph._extract_error_file_refs(attempt_history)
        if error_file_refs:
            DevMasterGraph._record_error_file_refs(state, error_file_refs)
            DevMasterGraph._emit_event(
                state,
                "validation_error_file_refs",
                refs=error_file_refs,
            )
        for outcome in outcomes:
            checklist_id = f"todo_{outcome.get('task_id', '')}"
            status = "completed" if outcome.get("status") == "completed" else "failed"
            DevMasterGraph._set_checklist_status(state, checklist_id, status, evidence=outcome)
        if pending:
            state["errors"].append(f"[VALIDATION] pending llm recovery unsupported for validation: {pending.get('task_id')}")
        if errors or pending:
            state["errors"].extend(errors)
            if error_file_refs:
                state["errors"].append(
                    "[RECOVERABLE_CONTEXT_GAP] validation failed with file-level diagnostics; "
                    f"targeted fix candidates={error_file_refs[:8]}"
                )
            state["validation_status"] = "failed"
            state["phase_status"]["execute_validation_phase"] = "failed"
            DevMasterGraph._emit(state, "[VALIDATION] failed")
            return state
        state["validation_status"] = "completed"
        state["phase_status"]["execute_validation_phase"] = "completed"
        DevMasterGraph._emit(state, "[VALIDATION] completed")
        return state
