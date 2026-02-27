from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from services.dev.command_policy import assess_risk, detect_stack_from_command, normalize_command_for_stack
from services.dev.executor_policy import _is_blocked_command, _is_likely_long_running_command, _violates_constraints
from services.dev.executor_rewrite import classify_failure, rewrite_command_deterministic
from services.dev.executor_runtime import _run_once
from services.dev.executor_scope import DevExecutorError, _normalize_scope_path, _resolve_cwd
from services.dev.executor_telemetry import _emit, _emit_event, _sanitize_log_value
from services.dev.types.executor_types import ExecuteDevTasksResult
from shared.dev_schemas import DevTask


def execute_dev_tasks(
    tasks: List[DevTask],
    *,
    scope_root: str,
    max_retries: int = 5,
    reserve_last_for_llm: bool = True,
    timeout_seconds: int = 900,
    log_sink: Optional[Callable[[str], None]] = None,
    heartbeat_seconds: float = 15.0,
    ask_confirmation: Optional[Callable[[str], bool]] = None,
    ask_runtime_prompt: Optional[Callable[[str], str]] = None,
    event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    stack_hint: str = "generic",
    interactive_prompt_timeout_seconds: float = 60.0,
    constraints: Optional[List[str]] = None,
    command_run_mode: Literal["terminating", "service_smoke", "auto"] = "terminating",
) -> ExecuteDevTasksResult:
    logs: List[str] = []
    touched_paths: List[str] = []
    errors: List[str] = []
    attempt_history: List[Dict[str, Any]] = []
    pending_llm_task: Optional[Dict[str, Any]] = None
    task_outcomes: List[Dict[str, Any]] = []
    active_constraints = constraints or []

    scope_abs = _normalize_scope_path(scope_root)
    os.makedirs(scope_abs, exist_ok=True)

    for task in tasks:
        if not task.command:
            _emit(logs, f"[SKIP] {task.id}: no command for task '{task.description}'", log_sink)
            _emit_event(
                logs,
                {"category": "task_skip", "task_id": task.id, "reason": "missing_command"},
                log_sink,
                event_sink,
            )
            continue

        if _is_blocked_command(task.command):
            errors.append(f"[BLOCKED] {task.id}: outbound push is disabled ('{task.command}')")
            _emit_event(
                logs,
                {
                    "category": "task_blocked",
                    "task_id": task.id,
                    "reason": "blocked_command",
                    "command": _sanitize_log_value(task.command, 200),
                },
                log_sink,
                event_sink,
            )
            break
        violated_reason = _violates_constraints(task.command, active_constraints)
        if violated_reason:
            errors.append(f"[BLOCKED] {task.id}: {violated_reason} ('{task.command}')")
            _emit_event(
                logs,
                {
                    "category": "task_blocked",
                    "task_id": task.id,
                    "reason": "constraint_violation",
                    "detail": _sanitize_log_value(violated_reason, 200),
                    "command": _sanitize_log_value(task.command, 200),
                },
                log_sink,
                event_sink,
            )
            break
        is_risky, reason = assess_risk(task.command)
        if is_risky:
            if callable(ask_confirmation):
                approved = bool(ask_confirmation(f"Approve risky command for {task.id}? {task.command} ({reason})"))
                if not approved:
                    errors.append(f"[BLOCKED] {task.id}: risky command not approved ('{task.command}')")
                    break
            else:
                errors.append(f"[BLOCKED] {task.id}: risky command requires confirmation ('{task.command}')")
                break

        try:
            cwd = _resolve_cwd(scope_abs, task.cwd or ".")
        except DevExecutorError as e:
            errors.append(f"[SCOPE] {task.id}: {e}")
            break

        os.makedirs(cwd, exist_ok=True)
        touched_paths.append(cwd)
        _emit(logs, f"[TASK] id={task.id} kind={task.kind} cwd={cwd}", log_sink)
        _emit(logs, f"[WHY_THIS_STEP] {task.description}", log_sink)
        llm_reserved = 1 if reserve_last_for_llm else 0
        deterministic_budget = max(1, max_retries - llm_reserved)
        inferred_stack = stack_hint or detect_stack_from_command(task.command)
        current_command = normalize_command_for_stack(task.command, inferred_stack)
        _emit_event(
            logs,
            {
                "category": "command_provenance",
                "task_id": task.id,
                "task_kind": task.kind,
                "cwd": cwd,
                "stack_hint": stack_hint,
                "inferred_stack": inferred_stack,
                "original_command": _sanitize_log_value(task.command, 240),
                "normalized_command": _sanitize_log_value(current_command, 240),
                "constraints_count": len(active_constraints),
            },
            log_sink,
            event_sink,
        )
        attempted_commands: List[str] = []
        last_error: Optional[str] = None
        last_attempt: Optional[Dict[str, Any]] = None

        for attempt_idx in range(1, deterministic_budget + 1):
            strategy = "original" if attempt_idx == 1 else "deterministic_rewrite"
            attempted_commands.append(current_command)
            effective_run_mode: Literal["terminating", "service_smoke"]
            if command_run_mode == "auto":
                effective_run_mode = "service_smoke" if _is_likely_long_running_command(current_command) else "terminating"
            else:
                effective_run_mode = command_run_mode
            attempt_logs, run_error, attempt = _run_once(
                task_id=task.id,
                task_kind=task.kind,
                cwd=cwd,
                command=current_command,
                timeout_seconds=timeout_seconds,
                log_sink=log_sink,
                heartbeat_seconds=heartbeat_seconds,
                ask_runtime_prompt=ask_runtime_prompt,
                interactive_prompt_timeout_seconds=interactive_prompt_timeout_seconds,
                run_mode=effective_run_mode,
            )
            attempt["attempt"] = attempt_idx
            attempt["strategy"] = strategy
            logs.extend(attempt_logs)
            attempt_history.append(attempt)
            last_attempt = attempt
            last_error = run_error
            _emit_event(
                logs,
                {
                    "category": "run_attempt",
                    "task_id": task.id,
                    "attempt": attempt_idx,
                    "strategy": strategy,
                    "command": _sanitize_log_value(current_command, 240),
                    "exit_code": attempt.get("exit_code"),
                    "failure_category": attempt.get("category", "unknown"),
                    "elapsed_ms": attempt.get("elapsed_ms", 0),
                    "run_mode": attempt.get("run_mode", effective_run_mode),
                    "smoke_ready": bool(attempt.get("smoke_ready", False)),
                    "stdout_preview": _sanitize_log_value(attempt.get("stdout", ""), 220),
                    "stderr_preview": _sanitize_log_value(attempt.get("stderr", ""), 220),
                },
                log_sink,
                event_sink,
            )

            if run_error is None:
                last_error = None
                break

            category = str(attempt.get("category", "unknown"))
            rewritten = rewrite_command_deterministic(
                current_command,
                category,
                inferred_stack,
                scope_root=scope_abs,
                cwd=cwd,
            )
            if rewritten == current_command:
                # No deterministic fix left; exit deterministic loop.
                _emit(
                    logs,
                    f"[WHY_RETRY_STOPPED] {task.id} no deterministic rewrite for category={category}"
                    ,
                    log_sink,
                )
                _emit_event(
                    logs,
                    {
                        "category": "retry_decision",
                        "task_id": task.id,
                        "attempt": attempt_idx,
                        "decision": "stop",
                        "reason": "no_deterministic_rewrite",
                        "failure_category": category,
                    },
                    log_sink,
                    event_sink,
                )
                break
            _emit(
                logs,
                f"[RETRY] {task.id} attempt {attempt_idx + 1}/{deterministic_budget} "
                f"category={category} strategy=deterministic_rewrite",
                log_sink,
            )
            _emit(
                logs,
                f"[WHY_RETRY] category={category} old_command={current_command} "
                f"new_command={rewritten}",
                log_sink,
            )
            _emit_event(
                logs,
                {
                    "category": "retry_decision",
                    "task_id": task.id,
                    "attempt": attempt_idx + 1,
                    "decision": "retry",
                    "reason": "deterministic_rewrite",
                    "failure_category": category,
                    "old_command": _sanitize_log_value(current_command, 200),
                    "new_command": _sanitize_log_value(rewritten, 200),
                },
                log_sink,
                event_sink,
            )
            current_command = rewritten

        if last_error is not None:
            if reserve_last_for_llm and last_attempt is not None:
                pending_llm_task = {
                    "task_id": task.id,
                    "task_kind": task.kind,
                    "cwd": cwd,
                    "last_command": current_command,
                    "last_error": last_error,
                    "last_attempt": last_attempt,
                    "attempted_commands": attempted_commands,
                    "max_retries": max_retries,
                }
                _emit(
                    logs,
                    f"[RETRY_EXHAUSTED] {task.id} deterministic budget exhausted; "
                    "eligible for LLM correction.",
                    log_sink,
                )
                _emit(
                    logs,
                    f"[ATTEMPT_SUMMARY] last_category={last_attempt.get('category')} "
                    f"elapsed_ms={last_attempt.get('elapsed_ms')}",
                    log_sink,
                )
                task_outcomes.append(
                    {
                        "task_id": task.id,
                        "status": "pending_llm",
                        "command": current_command,
                        "cwd": cwd,
                        "category": last_attempt.get("category", "unknown"),
                        "exit_code": last_attempt.get("exit_code"),
                        "elapsed_ms": last_attempt.get("elapsed_ms", 0),
                        "run_mode": last_attempt.get("run_mode", "terminating"),
                        "evidence": {"attempted_commands": attempted_commands},
                        "stdout_excerpt": _sanitize_log_value((last_attempt or {}).get("stdout", ""), 800),
                        "stderr_excerpt": _sanitize_log_value((last_attempt or {}).get("stderr", ""), 800),
                    }
                )
                _emit_event(
                    logs,
                    {
                        "category": "task_outcome",
                        "task_id": task.id,
                        "status": "pending_llm",
                        "failure_category": last_attempt.get("category", "unknown"),
                    },
                    log_sink,
                    event_sink,
                )
            else:
                errors.append(last_error)
                task_outcomes.append(
                    {
                        "task_id": task.id,
                        "status": "failed",
                        "command": current_command,
                        "cwd": cwd,
                        "category": (last_attempt or {}).get("category", "unknown"),
                        "exit_code": (last_attempt or {}).get("exit_code"),
                        "elapsed_ms": (last_attempt or {}).get("elapsed_ms", 0),
                        "run_mode": (last_attempt or {}).get("run_mode", "terminating"),
                        "evidence": {"attempted_commands": attempted_commands},
                        "stdout_excerpt": _sanitize_log_value((last_attempt or {}).get("stdout", ""), 800),
                        "stderr_excerpt": _sanitize_log_value((last_attempt or {}).get("stderr", ""), 800),
                    }
                )
                _emit_event(
                    logs,
                    {
                        "category": "task_outcome",
                        "task_id": task.id,
                        "status": "failed",
                        "failure_category": (last_attempt or {}).get("category", "unknown"),
                        "error": _sanitize_log_value(last_error, 220),
                    },
                    log_sink,
                    event_sink,
                )
            break
        task_outcomes.append(
            {
                "task_id": task.id,
                "status": "completed",
                "command": current_command,
                "cwd": cwd,
                "category": (last_attempt or {}).get("category", "none"),
                "exit_code": (last_attempt or {}).get("exit_code", 0),
                "elapsed_ms": (last_attempt or {}).get("elapsed_ms", 0),
                "run_mode": (last_attempt or {}).get("run_mode", "terminating"),
                "evidence": {
                    "attempts": len(attempted_commands),
                    "smoke_ready": bool((last_attempt or {}).get("smoke_ready", False)),
                },
                "stdout_excerpt": _sanitize_log_value((last_attempt or {}).get("stdout", ""), 800),
                "stderr_excerpt": _sanitize_log_value((last_attempt or {}).get("stderr", ""), 800),
            }
        )
        _emit_event(
            logs,
            {
                "category": "task_outcome",
                "task_id": task.id,
                "status": "completed",
                "attempts": len(attempted_commands),
                "run_mode": (last_attempt or {}).get("run_mode", "terminating"),
            },
            log_sink,
            event_sink,
        )

    return logs, touched_paths, errors, attempt_history, pending_llm_task, task_outcomes
