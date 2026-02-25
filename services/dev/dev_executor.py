from __future__ import annotations

import os
import subprocess
from typing import List, Tuple

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

    if raw.startswith("projects/") or raw.startswith("projects\\"):
        raw = raw.split("/", 1)[1] if "/" in raw else raw.split("\\", 1)[1]

    if os.path.isabs(raw):
        return _assert_within_scope(scope_root, raw)
    return _assert_within_scope(scope_root, os.path.join(scope_root, raw))


def _is_blocked_command(command: str) -> bool:
    low = command.lower()
    return "git push" in low


def execute_dev_tasks(
    tasks: List[DevTask],
    *,
    scope_root: str,
) -> Tuple[List[str], List[str], List[str]]:
    logs: List[str] = []
    touched_paths: List[str] = []
    errors: List[str] = []

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
        logs.append(f"[RUN] {task.id} ({task.kind}) @ {cwd}: {task.command}")
        try:
            proc = subprocess.run(
                task.command,
                cwd=cwd,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.stdout:
                logs.append(f"[STDOUT] {task.id}\n{proc.stdout.strip()}")
            if proc.stderr:
                logs.append(f"[STDERR] {task.id}\n{proc.stderr.strip()}")
            if proc.returncode != 0:
                errors.append(f"[FAIL] {task.id}: exited with code {proc.returncode}")
                break
        except Exception as e:
            errors.append(f"[EXCEPTION] {task.id}: {e}")
            break

    return logs, touched_paths, errors
