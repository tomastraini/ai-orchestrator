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


class FinalCompileGateMixin:
    @staticmethod
    def _execute_final_compile_gate_impl(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_final_compile_gate"
        DevMasterGraph._emit(state, "[PHASE_START] execute_final_compile_gate")
        DevMasterGraph._ensure_repository_memory(state)
        if (
            state.get("bootstrap_status") == "failed"
            or state.get("implementation_status") == "failed"
            or state.get("validation_status") == "failed"
        ):
            state["final_compile_status"] = "skipped"
            state["phase_status"]["execute_final_compile_gate"] = "skipped"
            DevMasterGraph._emit(state, "[FINAL_COMPILE] skipped due to previous failure")
            return state

        compile_tasks = state.get("final_compile_tasks", [])
        had_compile_tasks = bool(compile_tasks)
        filtered_compile_tasks: List[DevTask] = []
        for task in compile_tasks:
            item = DevMasterGraph._find_checklist_item(state, f"todo_{task.id}")
            if item and str(item.get("status", "")) == "completed":
                continue
            filtered_compile_tasks.append(task)
        compile_tasks = filtered_compile_tasks
        if had_compile_tasks and not compile_tasks:
            state["final_compile_status"] = "completed"
            state["phase_status"]["execute_final_compile_gate"] = "completed"
            DevMasterGraph._emit(state, "[FINAL_COMPILE] all compile checklist items already completed")
            return state
        if not compile_tasks:
            active_project_root = str(state.get("active_project_root", "")).strip()
            stacks = [str(x) for x in state.get("detected_stacks", []) if isinstance(x, str)]
            inferred_compile = DevMasterGraph._infer_final_compile_commands(
                project_dir=active_project_root,
                stacks=stacks,
                validation_commands=[],
            )
            if inferred_compile:
                compile_tasks = [
                    DevTask(
                        id=f"final_compile_fallback_{idx+1}",
                        description=f"run fallback final compile gate: {cmd}",
                        command=cmd,
                        cwd=active_project_root or str(state.get("project_root", "")),
                        kind="validation",
                    )
                    for idx, cmd in enumerate(inferred_compile)
                ]
                DevMasterGraph._emit_event(
                    state,
                    "final_compile_fallback_inferred",
                    commands=inferred_compile,
                )
            else:
                state["errors"].append("[FINAL_COMPILE] no terminating compile/build command inferred.")
                state["needs_validation_clarification"] = True
                DevMasterGraph._emit_event(
                    state,
                    "compile_inference_missing",
                    reason="no_terminating_compile_command_inferred",
                    followup_options=state.get("validation_followup_options", []),
                )
                state["final_compile_status"] = "failed"
                state["phase_status"]["execute_final_compile_gate"] = "failed"
                return state

        active_root = str(state.get("active_project_root", "")).strip()
        if active_root:
            compile_tasks = [
                DevTask(
                    id=task.id,
                    description=task.description,
                    command=task.command,
                    cwd=active_root,
                    kind=task.kind,
                )
                for task in compile_tasks
            ]

        for task in compile_tasks:
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{task.id}",
                "in_progress",
                evidence={"phase": "final_compile", "task_id": task.id},
            )

        logs, touched_paths, errors, attempt_history, pending, outcomes = execute_dev_tasks(
            compile_tasks,
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
            command_run_mode="terminating",
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
                        "phase": "final_compile",
                        "task_id": outcome.get("task_id"),
                        "category": outcome.get("category", "unknown"),
                        "exit_code": outcome.get("exit_code"),
                        "stdout_excerpt": outcome.get("stdout_excerpt", ""),
                        "stderr_excerpt": outcome.get("stderr_excerpt", ""),
                    },
                )
        compile_error_file_refs = DevMasterGraph._extract_error_file_refs(attempt_history)
        if compile_error_file_refs:
            DevMasterGraph._record_error_file_refs(state, compile_error_file_refs)
            DevMasterGraph._emit_event(
                state,
                "final_compile_error_file_refs",
                refs=compile_error_file_refs,
            )
        taxonomy = DevMasterGraph._classify_diagnostic_taxonomy(attempt_history)
        if taxonomy:
            DevMasterGraph._remember(
                state,
                "correction_attempts",
                {
                    "phase": "final_compile",
                    "taxonomy": taxonomy,
                    "file_refs": compile_error_file_refs,
                },
            )
            DevMasterGraph._emit_event(
                state,
                "final_compile_failure_taxonomy",
                taxonomy=taxonomy,
            )
        for outcome in outcomes:
            checklist_id = f"todo_{outcome.get('task_id', '')}"
            status = "completed" if outcome.get("status") == "completed" else "failed"
            DevMasterGraph._set_checklist_status(state, checklist_id, status, evidence=outcome)
        if pending:
            errors.append(f"[FINAL_COMPILE] pending llm recovery unsupported for final compile: {pending.get('task_id')}")
        if errors:
            signatures = DevMasterGraph._extract_deterministic_failure_signatures(attempt_history)
            if signatures:
                DevMasterGraph._emit_event(
                    state,
                    "deterministic_failure_signatures",
                    phase="final_compile",
                    signatures=signatures[:12],
                )
            stacks = [str(x) for x in state.get("detected_stacks", []) if isinstance(x, str)]
            active_project_root = str(state.get("active_project_root", "")).strip()
            recovery_commands = DevMasterGraph._infer_compile_recovery_commands(
                stacks=stacks,
                taxonomy=taxonomy,
                project_dir=active_project_root,
            )
            attempted_commands_lower = {
                str(item.get("command", "")).strip().lower()
                for item in attempt_history
                if isinstance(item, dict) and str(item.get("command", "")).strip()
            }
            filtered_recovery_commands: List[str] = []
            rejected_recovery: List[Dict[str, Any]] = []
            for cmd in recovery_commands:
                cmd_low = str(cmd).strip().lower()
                if cmd_low in attempted_commands_lower:
                    rejected_recovery.append({"command": cmd, "reason": "identical_command_without_new_evidence"})
                    continue
                filtered_recovery_commands.append(cmd)
            if rejected_recovery:
                DevMasterGraph._emit_event(
                    state,
                    "recovery_command_rejected",
                    phase="final_compile",
                    rejections=rejected_recovery,
                )
            recovery_commands = filtered_recovery_commands
            if recovery_commands:
                recovery_tasks = [
                    DevTask(
                        id=f"final_compile_recovery_{idx+1}",
                        description=f"targeted compile recovery: {cmd}",
                        command=cmd,
                        cwd=active_project_root or str(state.get("project_root", "")),
                        kind="validation",
                    )
                    for idx, cmd in enumerate(recovery_commands)
                ]
                DevMasterGraph._emit_event(
                    state,
                    "final_compile_recovery_started",
                    taxonomy=taxonomy,
                    commands=recovery_commands,
                )
                rec_logs, rec_touched, rec_errors, rec_attempts, rec_pending, rec_outcomes = execute_dev_tasks(
                    recovery_tasks,
                    scope_root=state["scope_root"],
                    max_retries=1,
                    reserve_last_for_llm=False,
                    log_sink=state.get("log_sink"),
                    stack_hint=(state.get("detected_stacks") or ["generic"])[0],
                    constraints=[
                        str(x).strip()
                        for x in state.get("plan", {}).get("constraints", [])
                        if isinstance(x, str) and str(x).strip()
                    ],
                    command_run_mode="terminating",
                    event_sink=(lambda event: DevMasterGraph._emit_event(state, "executor_event", **event)),
                )
                state["logs"].extend(rec_logs)
                state["touched_paths"].extend(rec_touched)
                state["attempt_history"].extend(rec_attempts)
                state["task_outcomes"].extend(rec_outcomes)
                if rec_pending:
                    rec_errors.append(f"pending recovery unsupported: {rec_pending.get('task_id')}")
                if not rec_errors:
                    state["final_compile_status"] = "completed"
                    state["phase_status"]["execute_final_compile_gate"] = "completed"
                    DevMasterGraph._emit(state, "[FINAL_COMPILE] recovered")
                    return state
                DevMasterGraph._emit_event(
                    state,
                    "failure_replanned",
                    phase="final_compile",
                    reason="recovery_attempt_failed",
                    attempted_commands=[str(t.command) for t in recovery_tasks],
                    retryable=True,
                )
            else:
                state["capability_gaps"] = list(state.get("capability_gaps", [])) + [
                    {
                        "phase": "final_compile",
                        "reason": "no_safe_recovery_command",
                        "taxonomy": taxonomy,
                        "errors": errors[-2:],
                    }
                ]
                DevMasterGraph._emit_event(
                    state,
                    "failure_replanned",
                    phase="final_compile",
                    reason="no_safe_recovery_command",
                    retryable=True,
                    taxonomy=taxonomy,
                )
            if compile_error_file_refs:
                state["errors"].append(
                    "[RECOVERABLE_CONTEXT_GAP] final compile failed with file-level diagnostics; "
                    f"targeted fix candidates={compile_error_file_refs[:8]}"
                )
            state["errors"].extend(errors)
            state["final_compile_status"] = "failed"
            state["phase_status"]["execute_final_compile_gate"] = "failed"
            DevMasterGraph._emit_event(
                state,
                "degraded_continue",
                phase="final_compile",
                status="failed_non_terminal",
                next_action="continue_with_partial_progress",
            )
            DevMasterGraph._emit(state, "[FINAL_COMPILE] failed")
            return state

        state["final_compile_status"] = "completed"
        state["phase_status"]["execute_final_compile_gate"] = "completed"
        DevMasterGraph._emit(state, "[FINAL_COMPILE] completed")
        return state
