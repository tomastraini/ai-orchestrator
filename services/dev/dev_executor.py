from __future__ import annotations

import os
import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.dev.command_policy import assess_risk, normalize_non_interactive
from shared.dev_schemas import DevTask


class DevExecutorError(RuntimeError):
    pass


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

    raw_norm = raw.replace("\\", "/")
    while raw_norm.startswith("projects/"):
        raw_norm = raw_norm.split("/", 1)[1] if "/" in raw_norm else ""
    raw = raw_norm or "."

    if os.path.isabs(raw):
        return _assert_within_scope(scope_root, raw)
    return _assert_within_scope(scope_root, os.path.join(scope_root, raw))


def _is_blocked_command(command: str) -> bool:
    low = command.lower()
    return "git push" in low


def _emit(logs: List[str], message: str, log_sink: Optional[Callable[[str], None]]) -> None:
    logs.append(message)
    if callable(log_sink):
        try:
            log_sink(message)
        except Exception:
            # Log streaming should never break execution.
            pass


def classify_failure(stdout: str, stderr: str, exit_code: int) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if "ok to proceed?" in text or "npm error canceled" in text or "prompt" in text:
        return "interactive_prompt"
    if "not recognized as an internal or external command" in text or "command not found" in text:
        return "command_not_found"
    if "no such file or directory" in text or "cannot find the path specified" in text:
        return "path_issue"
    if "package manager" in text or "npm" in text or "yarn" in text or "pnpm" in text:
        return "package_manager_mismatch"
    if exit_code != 0:
        return "unknown"
    return "none"


def rewrite_command_deterministic(command: str, category: str) -> str:
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

    # Normalize known bootstrap generators to non-interactive npm defaults.
    if "create-react-app" in low:
        if "--use-npm" not in low:
            cmd = f"{cmd} --use-npm"
        return cmd

    if "nest new" in low and "@nestjs/cli" not in low:
        # Convert to deterministic non-interactive npx form.
        parts = cmd.split()
        app_name = "app"
        if len(parts) >= 3:
            app_name = parts[2]
        return f"npx @nestjs/cli new {app_name} --package-manager npm --skip-git"

    if "@nestjs/cli new" in low:
        if "--package-manager" not in low:
            cmd = f"{cmd} --package-manager npm"
        if "--skip-git" not in low:
            cmd = f"{cmd} --skip-git"
        return cmd

    if category == "interactive_prompt":
        if low.startswith("npx ") and "--yes" not in low:
            return f"npx --yes {cmd[4:].strip()}"
        if " npm " in f" {low} " and "--yes" not in low:
            return f"{cmd} --yes"

    return normalize_non_interactive(cmd)


def _run_once(
    *,
    task_id: str,
    task_kind: str,
    cwd: str,
    command: str,
    timeout_seconds: int,
    log_sink: Optional[Callable[[str], None]] = None,
    heartbeat_seconds: float = 15.0,
) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
    logs: List[str] = []
    started = time.time()
    _emit(logs, f"[RUN] {task_id} ({task_kind}) @ {cwd}: {command}", log_sink)
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            shell=True,
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
        timeout_at = started + float(timeout_seconds)
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
                last_activity = now
            except Empty:
                pass

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
        elapsed_ms = int((time.time() - started) * 1000)
        stdout = "\n".join(chunk for chunk in stdout_chunks if chunk).strip()
        stderr = "\n".join(chunk for chunk in stderr_chunks if chunk).strip()
        if stdout:
            _emit(logs, f"[STDOUT] {task_id}\n{stdout}", log_sink)
        if stderr:
            _emit(logs, f"[STDERR] {task_id}\n{stderr}", log_sink)
        exit_code = int(proc.returncode if proc.returncode is not None else 1)
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
        }
        if exit_code == 0:
            _emit(logs, f"[DONE] {task_id} in {elapsed_ms}ms", log_sink)
            return logs, None, attempt
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
) -> Tuple[List[str], List[str], List[str], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    logs: List[str] = []
    touched_paths: List[str] = []
    errors: List[str] = []
    attempt_history: List[Dict[str, Any]] = []
    pending_llm_task: Optional[Dict[str, Any]] = None

    scope_abs = _normalize_scope_path(scope_root)
    os.makedirs(scope_abs, exist_ok=True)

    for task in tasks:
        if not task.command:
            _emit(logs, f"[SKIP] {task.id}: no command for task '{task.description}'", log_sink)
            continue

        if _is_blocked_command(task.command):
            errors.append(f"[BLOCKED] {task.id}: outbound push is disabled ('{task.command}')")
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
        current_command = task.command
        attempted_commands: List[str] = []
        last_error: Optional[str] = None
        last_attempt: Optional[Dict[str, Any]] = None

        for attempt_idx in range(1, deterministic_budget + 1):
            strategy = "original" if attempt_idx == 1 else "deterministic_rewrite"
            attempted_commands.append(current_command)
            attempt_logs, run_error, attempt = _run_once(
                task_id=task.id,
                task_kind=task.kind,
                cwd=cwd,
                command=current_command,
                timeout_seconds=timeout_seconds,
                log_sink=log_sink,
                heartbeat_seconds=heartbeat_seconds,
            )
            attempt["attempt"] = attempt_idx
            attempt["strategy"] = strategy
            logs.extend(attempt_logs)
            attempt_history.append(attempt)
            last_attempt = attempt
            last_error = run_error

            if run_error is None:
                last_error = None
                break

            category = str(attempt.get("category", "unknown"))
            rewritten = rewrite_command_deterministic(current_command, category)
            if rewritten == current_command:
                # No deterministic fix left; exit deterministic loop.
                _emit(
                    logs,
                    f"[WHY_RETRY_STOPPED] {task.id} no deterministic rewrite for category={category}"
                    ,
                    log_sink,
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
            else:
                errors.append(last_error)
            break

    return logs, touched_paths, errors, attempt_history, pending_llm_task


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
) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
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
    )
