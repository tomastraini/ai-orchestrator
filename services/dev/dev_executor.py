from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import json
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from services.dev.command_policy import (
    assess_risk,
    detect_stack_from_command,
    normalize_command_for_stack,
    normalize_non_interactive,
)
from services.dev.types.executor_types import ExecuteDevTasksResult, RecoveryRunResult, RunOnceResult
from shared.pathing import _collapse_nested_projects_segments, canonicalize_scope_path
from shared.dev_schemas import DevTask


class DevExecutorError(RuntimeError):
    pass


PROMPT_REGEX = re.compile(
    r"(ok to proceed\??|proceed\??|\[y/n\]|\(y/n\)|\(y/N\)|\(Y/n\)|confirm\??)",
    re.IGNORECASE,
)
SERVICE_READY_REGEX = re.compile(
    r"(ready in|localhost:|listening on|started|running at|vite v\d)",
    re.IGNORECASE,
)
SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
]


def _normalize_scope_path(path: str) -> str:
    return os.path.abspath(os.path.normpath(path))


def _assert_within_scope(scope_root: str, candidate_path: str) -> str:
    scope_abs = _normalize_scope_path(scope_root)
    candidate_abs = _normalize_scope_path(candidate_path)
    if os.path.commonpath([scope_abs, candidate_abs]) != scope_abs:
        raise DevExecutorError(
            f"Path '{candidate_path}' escapes allowed scope '{scope_root}'."
        )
    return candidate_abs


def _resolve_cwd(scope_root: str, raw_cwd: str) -> str:
    raw = (raw_cwd or "").strip()
    if not raw or raw == "." or raw == "projects":
        return _assert_within_scope(scope_root, scope_root)

    raw_norm = _collapse_nested_projects_segments(raw.replace("\\", "/"))
    if raw_norm == "projects":
        raw_norm = "."
    while raw_norm.startswith("projects/"):
        raw_norm = raw_norm.split("/", 1)[1] if "/" in raw_norm else "."
    raw = raw_norm or "."

    if os.path.isabs(raw):
        return _assert_within_scope(scope_root, canonicalize_scope_path(scope_root, raw))
    return _assert_within_scope(scope_root, canonicalize_scope_path(scope_root, os.path.join(scope_root, raw)))


def _is_blocked_command(command: str) -> bool:
    _ = command
    return False


def _violates_constraints(command: str, constraints: List[str]) -> Optional[str]:
    low_cmd = f" {command.lower()} "
    for raw_constraint in constraints:
        constraint = str(raw_constraint or "").strip().lower()
        if not constraint:
            continue
        if ("no git push" in constraint or "do not push" in constraint) and " git push " in low_cmd:
            return f"violates constraint '{raw_constraint}'"
        if "no git" in constraint and " git " in low_cmd:
            return f"violates constraint '{raw_constraint}'"
        if (
            "no dev server" in constraint
            or "do not run dev server" in constraint
            or "do not start server" in constraint
            or "no npm start" in constraint
        ) and any(token in low_cmd for token in [" npm start ", " npm run dev ", " pnpm dev ", " yarn dev ", " vite "]):
            return f"violates constraint '{raw_constraint}'"
        if ("no install" in constraint or "do not install" in constraint) and any(
            token in low_cmd for token in [" npm install ", " pnpm install ", " yarn install ", " pip install "]
        ):
            return f"violates constraint '{raw_constraint}'"
    return None


def _emit(logs: List[str], message: str, log_sink: Optional[Callable[[str], None]]) -> None:
    logs.append(message)
    if callable(log_sink):
        try:
            log_sink(message)
        except Exception:
            # Log streaming should never break execution.
            pass


def _sanitize_log_value(value: Any, max_length: int = 500) -> str:
    text = str(value or "")
    if len(text) > max_length:
        text = f"{text[:max_length]}... [truncated {len(text) - max_length} chars]"
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


def _emit_event(
    logs: List[str],
    event: Dict[str, Any],
    log_sink: Optional[Callable[[str], None]],
    event_sink: Optional[Callable[[Dict[str, Any]], None]],
) -> None:
    payload = dict(event)
    _emit(logs, f"[EVENT] {json.dumps(payload, sort_keys=True)}", log_sink)
    if callable(event_sink):
        try:
            event_sink(payload)
        except Exception:
            pass


def _is_likely_long_running_command(command: str) -> bool:
    low = f" {str(command or '').lower()} "
    tokens = [
        " npm run dev ",
        " npm start ",
        " pnpm dev ",
        " yarn dev ",
        " vite ",
        " next dev ",
        " flask run ",
        " uvicorn ",
        " rails server ",
        " dotnet watch ",
    ]
    return any(token in low for token in tokens)


def classify_failure(stdout: str, stderr: str, exit_code: int) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if "operation cancelled" in text or "operation canceled" in text or "aborted" in text:
        return "operation_cancelled"
    if "ok to proceed?" in text or "npm error canceled" in text or "prompt" in text:
        return "interactive_prompt"
    if "not recognized as an internal or external command" in text or "command not found" in text:
        return "command_not_found"
    if "no such file or directory" in text or "cannot find the path specified" in text or "enoent" in text:
        return "path_issue"
    if (
        "syntaxerror" in text
        or "unexpected token" in text
        or "parse error" in text
        or "error ts" in text
        or "typescript" in text
        or "esbuild" in text
        or "vite build" in text
    ):
        return "syntax_or_compile_error"
    if "cannot find module" in text:
        return "module_resolution_error"
    if "test failed" in text or "assertionerror" in text or "failing tests" in text:
        return "test_failure"
    if "config" in text or "tsconfig" in text or "package.json" in text or "pyproject" in text:
        return "config_error"
    if exit_code != 0 and any(tok in text for tok in ["package manager mismatch", "unsupported package manager", "unknown package manager"]):
        return "package_manager_mismatch"
    if exit_code != 0:
        return "unknown"
    return "none"


def rewrite_command_deterministic(
    command: str,
    category: str,
    stack_hint: str = "generic",
    *,
    scope_root: str = "",
    cwd: str = "",
) -> str:
    cmd = command.strip()
    low = cmd.lower()

    # Always strip brittle chained cwd changes; cwd is handled by executor.
    if "&&" in low:
        segments = [seg.strip() for seg in cmd.split("&&") if seg.strip()]
        filtered: List[str] = []
        for seg in segments:
            seg_low = seg.lower()
            if seg_low.startswith("cd "):
                continue
            if seg_low.startswith("mkdir ") or seg_low.startswith("mkdir -p "):
                continue
            filtered.append(seg)
        cmd = filtered[0] if filtered else ""
        low = cmd.lower()

    def _normalize_projects_target_token(token: str, cwd_hint: str) -> str:
        target = token.strip().replace("\\", "/").lstrip("./")
        cwd_norm = cwd_hint.strip().replace("\\", "/").lstrip("./")
        if not target.startswith("projects/"):
            return token
        # If cwd is projects root, keep child path (projects/foo -> foo), never collapse to "."
        if cwd_norm.endswith("/projects"):
            return target.split("/", 1)[1] if "/" in target else target
        if cwd_norm == "projects":
            return target.split("/", 1)[1] if "/" in target else target
        if cwd_norm in {"", "."}:
            return "."
        if target == cwd_norm:
            return "."
        if cwd_norm and target.startswith(f"{cwd_norm}/"):
            return target[len(cwd_norm) + 1 :] or "."
        return token

    # Keep deterministic rewrites generic in v2 (no framework-specific mutation).
    scope_abs = _normalize_scope_path(scope_root) if scope_root else ""
    cwd_rel = ""
    if cwd and scope_abs:
        try:
            cwd_rel = os.path.relpath(_normalize_scope_path(cwd), scope_abs).replace("\\", "/")
        except Exception:
            cwd_rel = ""
    parts = cmd.split()
    for i, tok in enumerate(parts):
        if tok.strip().replace("\\", "/").lstrip("./").startswith("projects/"):
            parts[i] = _normalize_projects_target_token(parts[i], cwd_rel or ".")
    cmd = " ".join(parts) if parts else cmd
    low = cmd.lower()

    if category == "interactive_prompt":
        if low.startswith("npx ") and "--yes" not in low:
            return f"npx --yes {cmd[4:].strip()}"
        if " npm " in f" {low} " and "--yes" not in low:
            return f"{cmd} --yes"

    return normalize_command_for_stack(normalize_non_interactive(cmd), stack_hint)


def _run_once(
    *,
    task_id: str,
    task_kind: str,
    cwd: str,
    command: str,
    timeout_seconds: int,
    log_sink: Optional[Callable[[str], None]] = None,
    heartbeat_seconds: float = 15.0,
    ask_runtime_prompt: Optional[Callable[[str], str]] = None,
    interactive_prompt_timeout_seconds: float = 60.0,
    run_mode: Literal["terminating", "service_smoke"] = "terminating",
) -> RunOnceResult:
    logs: List[str] = []
    started = time.time()
    _emit(logs, f"[RUN] {task_id} ({task_kind}) @ {cwd}: {command}", log_sink)
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        line_queue: Queue[Tuple[str, str]] = Queue()
        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []

        def _pump(pipe: Any, stream_name: str) -> None:
            if pipe is None:
                return
            try:
                for line in iter(pipe.readline, ""):
                    line_queue.put((stream_name, line.rstrip("\n")))
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t_out = threading.Thread(target=_pump, args=(proc.stdout, "stdout"), daemon=True)
        t_err = threading.Thread(target=_pump, args=(proc.stderr, "stderr"), daemon=True)
        t_out.start()
        t_err.start()

        last_activity = time.time()
        pending_prompt_started_at: Optional[float] = None
        timeout_at = started + float(timeout_seconds)
        smoke_ready = False
        while True:
            now = time.time()
            if now >= timeout_at:
                proc.kill()
                raise subprocess.TimeoutExpired(command, timeout_seconds)

            consumed = False
            try:
                stream_name, line = line_queue.get(timeout=0.2)
                consumed = True
                if stream_name == "stdout":
                    stdout_chunks.append(line)
                    _emit(logs, f"[STREAM_STDOUT] {task_id} {line}", log_sink)
                else:
                    stderr_chunks.append(line)
                    _emit(logs, f"[STREAM_STDERR] {task_id} {line}", log_sink)
                if PROMPT_REGEX.search(line):
                    prompt_text = line.strip()
                    _emit(logs, f"[INTERACTIVE_PROMPT] {task_id} detected prompt: {prompt_text}", log_sink)
                    if callable(ask_runtime_prompt):
                        user_answer = (ask_runtime_prompt(prompt_text) or "").strip()
                        normalized = user_answer.lower()
                        response = "y" if normalized in {"y", "yes", "true", "1", "ok", "approve"} else "n"
                    else:
                        _emit(
                            logs,
                            f"[INTERACTIVE_PROMPT] {task_id} no runtime callback; defaulting to safe reject",
                            log_sink,
                        )
                        response = "n"
                    try:
                        if proc.stdin is not None:
                            proc.stdin.write(f"{response}\n")
                            proc.stdin.flush()
                            _emit(
                                logs,
                                f"[INTERACTIVE_PROMPT] {task_id} forwarded response='{response}'",
                                log_sink,
                            )
                    except Exception as e:
                        _emit(logs, f"[INTERACTIVE_PROMPT_ERROR] {task_id} failed to send response: {e}", log_sink)
                    pending_prompt_started_at = now
                if run_mode == "service_smoke" and SERVICE_READY_REGEX.search(line):
                    smoke_ready = True
                    _emit(logs, f"[SERVICE_SMOKE_READY] {task_id} readiness signal detected", log_sink)
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                last_activity = now
            except Empty:
                pass

            if pending_prompt_started_at is not None:
                if (now - pending_prompt_started_at) >= interactive_prompt_timeout_seconds:
                    proc.kill()
                    _emit(
                        logs,
                        f"[INTERACTIVE_TIMEOUT] {task_id} unresolved prompt exceeded {interactive_prompt_timeout_seconds}s",
                        log_sink,
                    )
                    raise subprocess.TimeoutExpired(command, timeout_seconds)

            if proc.poll() is not None and line_queue.empty():
                break

            if not consumed and heartbeat_seconds > 0 and (now - last_activity) >= heartbeat_seconds:
                elapsed = int((now - started) * 1000)
                _emit(
                    logs,
                    f"[HEARTBEAT] {task_id} still running elapsed_ms={elapsed}",
                    log_sink,
                )
                last_activity = now

        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        elapsed_ms = int((time.time() - started) * 1000)
        stdout = "\n".join(chunk for chunk in stdout_chunks if chunk).strip()
        stderr = "\n".join(chunk for chunk in stderr_chunks if chunk).strip()
        if stdout:
            _emit(logs, f"[STDOUT] {task_id}\n{stdout}", log_sink)
        if stderr:
            _emit(logs, f"[STDERR] {task_id}\n{stderr}", log_sink)
        exit_code = int(proc.returncode if proc.returncode is not None else 1)
        if run_mode == "service_smoke" and smoke_ready:
            # A smoke run is successful once readiness is observed.
            exit_code = 0
        category = classify_failure(stdout, stderr, exit_code)
        attempt = {
            "task_id": task_id,
            "command": command,
            "cwd": cwd,
            "exit_code": exit_code,
            "category": category,
            "elapsed_ms": elapsed_ms,
            "stdout": stdout,
            "stderr": stderr,
            "run_mode": run_mode,
            "smoke_ready": smoke_ready,
        }
        if exit_code == 0 and category in {"interactive_prompt", "package_manager_mismatch"}:
            category = "none"
            attempt["category"] = "none"
        if exit_code == 0 and category == "none":
            _emit(logs, f"[DONE] {task_id} in {elapsed_ms}ms", log_sink)
            return logs, None, attempt
        if exit_code == 0 and category != "none":
            return logs, f"[FAIL] {task_id}: semantic failure category={category}", attempt
        return logs, f"[FAIL] {task_id}: exited with code {exit_code}", attempt
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.time() - started) * 1000)
        attempt = {
            "task_id": task_id,
            "command": command,
            "cwd": cwd,
            "exit_code": None,
            "category": "timeout",
            "elapsed_ms": elapsed_ms,
            "stdout": "",
            "stderr": "Command timed out.",
            "run_mode": run_mode,
            "smoke_ready": False,
        }
        _emit(logs, f"[TIMEOUT] {task_id} exceeded {timeout_seconds}s", log_sink)
        return logs, f"[TIMEOUT] {task_id}: exceeded {timeout_seconds}s", attempt
    except Exception as e:
        elapsed_ms = int((time.time() - started) * 1000)
        attempt = {
            "task_id": task_id,
            "command": command,
            "cwd": cwd,
            "exit_code": None,
            "category": "exception",
            "elapsed_ms": elapsed_ms,
            "stdout": "",
            "stderr": str(e),
            "run_mode": run_mode,
            "smoke_ready": False,
        }
        _emit(logs, f"[EXCEPTION] {task_id}: {e}", log_sink)
        return logs, f"[EXCEPTION] {task_id}: {e}", attempt


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


def execute_single_recovery_command(
    *,
    task_id: str,
    task_kind: str,
    scope_root: str,
    cwd: str,
    command: str,
    timeout_seconds: int = 900,
    log_sink: Optional[Callable[[str], None]] = None,
    heartbeat_seconds: float = 15.0,
    ask_runtime_prompt: Optional[Callable[[str], str]] = None,
    interactive_prompt_timeout_seconds: float = 60.0,
    command_run_mode: Literal["terminating", "service_smoke"] = "terminating",
) -> RecoveryRunResult:
    scope_abs = _normalize_scope_path(scope_root)
    if _is_blocked_command(command):
        attempt = {
            "task_id": task_id,
            "attempt": 0,
            "strategy": "llm_rewrite",
            "command": command,
            "cwd": cwd,
            "exit_code": None,
            "category": "blocked",
            "elapsed_ms": 0,
            "stdout": "",
            "stderr": "Blocked command",
        }
        return [], f"[BLOCKED] {task_id}: outbound push is disabled ('{command}')", attempt

    safe_cwd = _resolve_cwd(scope_abs, cwd)
    return _run_once(
        task_id=task_id,
        task_kind=task_kind,
        cwd=safe_cwd,
        command=command,
        timeout_seconds=timeout_seconds,
        log_sink=log_sink,
        heartbeat_seconds=heartbeat_seconds,
        ask_runtime_prompt=ask_runtime_prompt,
        interactive_prompt_timeout_seconds=interactive_prompt_timeout_seconds,
        run_mode=command_run_mode,
    )
