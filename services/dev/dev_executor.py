from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

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

    return cmd


def _run_once(
    *,
    task_id: str,
    task_kind: str,
    cwd: str,
    command: str,
    timeout_seconds: int,
) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
    logs: List[str] = []
    started = time.time()
    logs.append(f"[RUN] {task_id} ({task_kind}) @ {cwd}: {command}")
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if stdout:
            logs.append(f"[STDOUT] {task_id}\n{stdout}")
        if stderr:
            logs.append(f"[STDERR] {task_id}\n{stderr}")
        category = classify_failure(stdout, stderr, proc.returncode)
        attempt = {
            "task_id": task_id,
            "command": command,
            "cwd": cwd,
            "exit_code": proc.returncode,
            "category": category,
            "elapsed_ms": elapsed_ms,
            "stdout": stdout,
            "stderr": stderr,
        }
        if proc.returncode == 0:
            logs.append(f"[DONE] {task_id} in {elapsed_ms}ms")
            return logs, None, attempt
        return logs, f"[FAIL] {task_id}: exited with code {proc.returncode}", attempt
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
        logs.append(f"[TIMEOUT] {task_id} exceeded {timeout_seconds}s")
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
        logs.append(f"[EXCEPTION] {task_id}: {e}")
        return logs, f"[EXCEPTION] {task_id}: {e}", attempt


def execute_dev_tasks(
    tasks: List[DevTask],
    *,
    scope_root: str,
    max_retries: int = 5,
    reserve_last_for_llm: bool = True,
    timeout_seconds: int = 900,
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
            logs.append(f"[SKIP] {task.id}: no command for task '{task.description}'")
            continue

        if _is_blocked_command(task.command):
            errors.append(f"[BLOCKED] {task.id}: outbound push is disabled ('{task.command}')")
            break

        try:
            cwd = _resolve_cwd(scope_abs, task.cwd or ".")
        except DevExecutorError as e:
            errors.append(f"[SCOPE] {task.id}: {e}")
            break

        os.makedirs(cwd, exist_ok=True)
        touched_paths.append(cwd)
        llm_reserved = 1 if reserve_last_for_llm else 0
        deterministic_budget = max(1, max_retries - llm_reserved)
        current_command = task.command
        last_error: Optional[str] = None
        last_attempt: Optional[Dict[str, Any]] = None

        for attempt_idx in range(1, deterministic_budget + 1):
            strategy = "original" if attempt_idx == 1 else "deterministic_rewrite"
            attempt_logs, run_error, attempt = _run_once(
                task_id=task.id,
                task_kind=task.kind,
                cwd=cwd,
                command=current_command,
                timeout_seconds=timeout_seconds,
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
                break
            logs.append(
                f"[RETRY] {task.id} attempt {attempt_idx + 1}/{deterministic_budget} "
                f"category={category} strategy=deterministic_rewrite"
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
                    "max_retries": max_retries,
                }
                logs.append(
                    f"[RETRY_EXHAUSTED] {task.id} deterministic budget exhausted; "
                    "eligible for LLM correction."
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
    )
