from __future__ import annotations

import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from services.dev.executor_policy import _is_blocked_command
from services.dev.executor_rewrite import classify_failure, rewrite_command_deterministic
from services.dev.executor_scope import _normalize_scope_path, _resolve_cwd
from services.dev.executor_telemetry import PROMPT_REGEX, SERVICE_READY_REGEX, _emit
from services.dev.types.executor_types import RecoveryRunResult, RunOnceResult


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
