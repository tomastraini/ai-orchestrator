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


class ImplementationExecutorMixin:
    @staticmethod
    def _file_sha1(path: str) -> str:
        if not os.path.exists(path) or not os.path.isfile(path):
            return ""
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()

    @staticmethod
    def _execute_bootstrap_phase_impl(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_bootstrap_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_bootstrap_phase")
        DevMasterGraph._emit(state, "[PHASE] bootstrap")
        if state.get("phase_status", {}).get("prepare_execution_steps") == "failed":
            state["bootstrap_status"] = "failed"
            state["phase_status"]["execute_bootstrap_phase"] = "skipped"
            DevMasterGraph._emit(state, "[BOOTSTRAP] skipped because prepare_execution_steps failed")
            return state
        next_item = DevMasterGraph._next_actionable_checklist_item(state)
        if next_item:
            DevMasterGraph._emit(
                state,
                f"[CHECKLIST] next_actionable={next_item.get('id')} kind={next_item.get('kind')}",
            )
        plan_constraints = [
            str(x).strip()
            for x in state.get("plan", {}).get("constraints", [])
            if isinstance(x, str) and str(x).strip()
        ]
        bootstrap_tasks = []
        for task in state.get("bootstrap_tasks", []):
            item = DevMasterGraph._find_checklist_item(state, f"todo_{task.id}")
            if item and str(item.get("status", "")) == "completed":
                continue
            bootstrap_tasks.append(task)
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{task.id}",
                "in_progress",
                evidence={"phase": "bootstrap", "task_id": task.id},
            )
        if not bootstrap_tasks:
            state["bootstrap_status"] = "completed"
            state["phase_status"]["execute_bootstrap_phase"] = "completed"
            DevMasterGraph._emit(state, "[BOOTSTRAP] no pending bootstrap checklist items")
            return state

        pending_llm_task = None
        errors: List[str] = []
        for task in bootstrap_tasks:
            logs, touched_paths, task_errors, attempt_history, task_pending_llm, outcomes = execute_dev_tasks(
                [task],
                scope_root=state["scope_root"],
                max_retries=int(state.get("max_retries", 5)),
                reserve_last_for_llm=True,
                log_sink=state.get("log_sink"),
                ask_confirmation=(lambda q: bool(state.get("ask_user")(q).strip().lower() in {"y", "yes", "true", "1"})) if callable(state.get("ask_user")) else None,
                ask_runtime_prompt=(lambda q: state.get("ask_user")(f"[DEV RUNTIME PROMPT] {q} (y/N)")) if callable(state.get("ask_user")) else None,
                stack_hint=(state.get("detected_stacks") or ["generic"])[0],
                interactive_prompt_timeout_seconds=60.0,
                constraints=plan_constraints,
                command_run_mode="auto",
                event_sink=(lambda event: DevMasterGraph._emit_event(state, "executor_event", **event)),
            )
            state["logs"].extend(logs)
            state["touched_paths"].extend(touched_paths)
            state["attempt_history"].extend(attempt_history)
            state["task_outcomes"].extend(outcomes)
            state["retry_count"] = len(state.get("attempt_history", []))
            for outcome in outcomes:
                checklist_id = f"todo_{outcome.get('task_id', '')}"
                status = "completed" if outcome.get("status") == "completed" else "blocked"
                DevMasterGraph._set_checklist_status(state, checklist_id, status, evidence=outcome)
            if outcomes and all(str(o.get("status")) == "completed" for o in outcomes):
                gate_ok, gate_evidence = DevMasterGraph._bootstrap_artifact_gate(state, task=task)
                DevMasterGraph._emit_event(
                    state,
                    "bootstrap_artifact_gate_pass" if gate_ok else "bootstrap_artifact_gate_fail",
                    task_id=task.id,
                    evidence=gate_evidence,
                )
                if not gate_ok:
                    task_errors.append(f"[BOOTSTRAP_INCOMPLETE] {gate_evidence.get('reason', 'artifact_gate_failed')}")
            # Mandatory per-handoff re-scan/re-index.
            root_evidence = DevMasterGraph._resolve_active_project_root_after_bootstrap(
                state=state,
                attempt_history=state.get("attempt_history", []),
            )
            state["root_resolution_evidence"] = root_evidence
            selected_root = str(root_evidence.get("selected_root", "")).strip()
            if selected_root:
                state["active_project_root"] = selected_root
            DevMasterGraph._refresh_active_root_index(state, category="post_handoff_index_refresh")
            if task_errors:
                errors.extend(task_errors)
                pending_llm_task = task_pending_llm
                break
            if task_pending_llm is not None:
                pending_llm_task = task_pending_llm
                break
        if errors:
            signatures = DevMasterGraph._extract_deterministic_failure_signatures(
                [x for x in state.get("attempt_history", []) if isinstance(x, dict)]
            )
            if signatures:
                DevMasterGraph._emit_event(
                    state,
                    "deterministic_failure_signatures",
                    phase="bootstrap",
                    signatures=signatures[:12],
                )
            timed_out_long_running = [
                attempt
                for attempt in state.get("attempt_history", [])
                if str(attempt.get("category", "")) == "timeout"
                and DevMasterGraph._is_long_running_validation_command(str(attempt.get("command", "")))
            ]
            if timed_out_long_running:
                unique_commands = sorted(
                    {
                        str(attempt.get("command", "")).strip()
                        for attempt in timed_out_long_running
                        if str(attempt.get("command", "")).strip()
                    }
                )
                timeout_note = (
                    "[BOOTSTRAP_SMOKE_TIMEOUT] One or more bootstrap dev-server commands "
                    "timed out before a readiness signal was detected. "
                    "Ensure the command prints a startup-ready indicator (for example, localhost URL or 'ready in'), "
                    "or move the command to validation if it is not required during bootstrap. "
                    f"commands={unique_commands}"
                )
                state["errors"].append(timeout_note)
                DevMasterGraph._emit(state, timeout_note)
            state["errors"].extend(errors)
            hard_failure = any("[FAIL]" in str(err) for err in errors) and not any(
                "BOOTSTRAP_INCOMPLETE" in str(err) for err in errors
            )
            state["bootstrap_status"] = "failed" if hard_failure else "blocked"
            state["last_error"] = errors[-1]
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            DevMasterGraph._emit_event(
                state,
                "terminal_failure_gate_rejected",
                phase="bootstrap",
                reason="recoverable_bootstrap_failure",
                errors=errors[-3:],
            )
            return state

        if pending_llm_task is None:
            root_evidence = DevMasterGraph._resolve_active_project_root_after_bootstrap(
                state=state,
                attempt_history=state.get("attempt_history", []),
            )
            state["root_resolution_evidence"] = root_evidence
            selected_root = str(root_evidence.get("selected_root", "")).strip()
            confidence = int(root_evidence.get("confidence", 0))
            ambiguous = bool(root_evidence.get("ambiguous", False))
            candidates = root_evidence.get("candidates", [])
            existing_root = str(state.get("active_project_root", "")).strip()
            trusted_existing = bool(existing_root and os.path.abspath(existing_root) == os.path.abspath(selected_root))
            DevMasterGraph._emit(state, f"[ROOT_EVIDENCE] confidence={confidence} candidates={candidates}")
            if (confidence < 45 or ambiguous) and not trusted_existing and len(candidates) >= 2 and callable(state.get("ask_user")):
                c1 = str(candidates[0].get("path", ""))
                c2 = str(candidates[1].get("path", ""))
                question = (
                    "Detected multiple possible project roots. Choose 1 or 2:\n"
                    f"1) {c1}\n"
                    f"2) {c2}"
                )
                answer = str(state.get("ask_user")(question)).strip().lower()
                if answer in {"1", "a"}:
                    selected_root = c1
                elif answer in {"2", "b"}:
                    selected_root = c2
                elif c1.lower() in answer:
                    selected_root = c1
                elif c2.lower() in answer:
                    selected_root = c2
                else:
                    state["errors"].append("[ROOT] unresolved root ambiguity from CLI answer.")
                    state["status"] = "bootstrap_failed"
                    state["bootstrap_status"] = "failed"
                    state["last_error"] = state["errors"][-1]
                    state["phase_status"]["execute_bootstrap_phase"] = "failed"
                    return state
            elif confidence < 45 and not trusted_existing and not callable(state.get("ask_user")):
                state["errors"].append("[ROOT] low confidence active root resolution without CLI clarification.")
                state["bootstrap_status"] = "blocked"
                state["last_error"] = state["errors"][-1]
                state["phase_status"]["execute_bootstrap_phase"] = "failed"
                return state

            state["active_project_root"] = selected_root
            state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
            DevMasterGraph._emit(state, f"[ROOT] active_project_root={state.get('active_project_root')}")
            state["bootstrap_status"] = "completed"
            DevMasterGraph._emit(
                state,
                f"[PHASE_SUMMARY] bootstrap attempts={len(state.get('attempt_history', []))} recovered=deterministic_or_clean"
            )
            state["phase_status"]["execute_bootstrap_phase"] = "completed"
            return state

        llm_corrector = state.get("llm_corrector")
        if not callable(llm_corrector):
            state["errors"].append(
                f"{pending_llm_task['last_error']} (No LLM corrector available after deterministic retries.)"
            )
            state["bootstrap_status"] = "blocked"
            state["last_error"] = state["errors"][-1]
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state

        if int(state.get("llm_calls_used", 0)) >= int(state.get("llm_call_budget", 0)):
            state["logs"].append(
                f"[LLM_BUDGET] reached ({state.get('llm_call_budget', 0)}); skipping correction."
            )
            state["errors"].append(
                f"{pending_llm_task['last_error']} (LLM model-call budget reached.)"
            )
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = state["errors"][-1]
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state

        correction_input = {
            "task_id": pending_llm_task["task_id"],
            "task_kind": pending_llm_task["task_kind"],
            "cwd": pending_llm_task["cwd"],
            "command": pending_llm_task["last_command"],
            "error": pending_llm_task["last_error"],
            "last_attempt": pending_llm_task["last_attempt"],
            "attempted_commands": pending_llm_task.get("attempted_commands", []),
            "scope_constraint": "All commands must remain within ./projects scope.",
            "push_constraint": "git push is blocked.",
            "execution_context": state.get("llm_context_contract", {}),
            "root_resolution_evidence": state.get("root_resolution_evidence", {}),
        }
        llm_started = time.time()
        try:
            state["llm_calls_used"] = int(state.get("llm_calls_used", 0)) + 1
            corrected_command = llm_corrector(correction_input).strip()
        except Exception as e:
            corrected_command = ""
            DevMasterGraph._emit(state, f"[LLM_REWRITE_ERROR] {pending_llm_task['task_id']}: {e}")
        llm_elapsed_ms = int((time.time() - llm_started) * 1000)
        DevMasterGraph._emit_event(
            state,
            "llm_call_meta",
            task_id=str(pending_llm_task.get("task_id", "")),
            call_index=int(state.get("llm_calls_used", 0)),
            prompt_chars=len(str(correction_input)),
            response_chars=len(str(corrected_command)),
            latency_ms=llm_elapsed_ms,
            success=bool(corrected_command),
        )

        if not corrected_command:
            state["errors"].append(
                f"{pending_llm_task['last_error']} (LLM did not provide corrected command.)"
            )
            state["bootstrap_status"] = "blocked"
            state["last_error"] = state["errors"][-1]
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state

        if not DevMasterGraph._recovery_satisfies_task_intent(
            str(pending_llm_task.get("task_kind", "")),
            str(pending_llm_task.get("last_command", "")),
            corrected_command,
        ):
            state["errors"].append(
                f"[RECOVERY_INTENT_MISMATCH] task={pending_llm_task.get('task_id')} "
                f"failed_command={pending_llm_task.get('last_command')} recovered_command={corrected_command}"
            )
            state["bootstrap_status"] = "blocked"
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            DevMasterGraph._emit_event(
                state,
                "recovery_intent_mismatch",
                task_id=str(pending_llm_task.get("task_id", "")),
                failed_command=str(pending_llm_task.get("last_command", "")),
                recovered_command=corrected_command,
            )
            return state

        DevMasterGraph._emit(state, f"[LLM_REWRITE] {pending_llm_task['task_id']} -> {corrected_command}")
        DevMasterGraph._emit(
            state,
            f"[WHY_RETRY] deterministic retries exhausted for task={pending_llm_task['task_id']}, using llm_rewrite"
        )
        recover_logs, recover_error, recover_attempt = execute_single_recovery_command(
            task_id=str(pending_llm_task["task_id"]),
            task_kind=str(pending_llm_task["task_kind"]),
            scope_root=state["scope_root"],
            cwd=str(pending_llm_task["cwd"]),
            command=corrected_command,
            log_sink=state.get("log_sink"),
            ask_runtime_prompt=(lambda q: state.get("ask_user")(f"[DEV RUNTIME PROMPT] {q} (y/N)")) if callable(state.get("ask_user")) else None,
            interactive_prompt_timeout_seconds=60.0,
        )
        recover_attempt["attempt"] = int(state.get("max_retries", 5))
        recover_attempt["strategy"] = "llm_rewrite"
        state["logs"].extend(recover_logs)
        state["attempt_history"].append(recover_attempt)
        state["retry_count"] = len(state.get("attempt_history", []))
        if recover_error:
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{pending_llm_task['task_id']}",
                "failed",
                evidence={"phase": "bootstrap", "error": recover_error},
            )
            state["errors"].append(recover_error)
            state["bootstrap_status"] = "blocked"
            state["last_error"] = recover_error
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state
        DevMasterGraph._set_checklist_status(
            state,
            f"todo_{pending_llm_task['task_id']}",
            "completed",
            evidence=recover_attempt,
        )
        state["bootstrap_status"] = "completed"
        root_evidence = DevMasterGraph._resolve_active_project_root_after_bootstrap(
            state=state,
            attempt_history=state.get("attempt_history", []),
        )
        state["root_resolution_evidence"] = root_evidence
        state["active_project_root"] = str(root_evidence.get("selected_root", state.get("active_project_root", ""))).strip()
        state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
        DevMasterGraph._emit(
            state,
            f"[ROOT_EVIDENCE] confidence={root_evidence.get('confidence', 0)} candidates={root_evidence.get('candidates', [])}",
        )
        DevMasterGraph._emit(state, f"[ROOT] active_project_root={state.get('active_project_root')}")
        DevMasterGraph._emit(
            state,
            f"[PHASE_SUMMARY] bootstrap attempts={len(state.get('attempt_history', []))} recovered=llm"
        )
        state["phase_status"]["execute_bootstrap_phase"] = "completed"
        return state

    def _resolve_implementation_path(scope_root: str, project_root: str, expected_path_hint: str) -> str:
        path = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        if path.startswith("projects/"):
            path = path.split("/", 1)[1] if "/" in path else ""
        elif project_root.startswith("projects/"):
            root_rel = project_root.split("/", 1)[1]
            path = f"{root_rel}/{path}".strip("/")
        safe_path = os.path.abspath(os.path.join(scope_root, path))
        scope_abs = os.path.abspath(scope_root)
        if os.path.commonpath([scope_abs, safe_path]) != scope_abs:
            raise RuntimeError(f"Implementation target escapes scope: {expected_path_hint}")
        return safe_path
