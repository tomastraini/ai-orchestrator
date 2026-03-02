from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


LogSinkFn = Callable[[str], None]


class ClaudeCodeCLIExecutor:
    """
    Runs Claude Code CLI as the implementation engine.
    """

    def __init__(self, repo_root: str) -> None:
        self.repo_root = repo_root
        self.default_timeout_seconds = int(os.getenv("CLAUDE_CODE_TIMEOUT_SECONDS", "1800"))
        self.preflight_enabled = str(os.getenv("CLAUDE_CODE_PREFLIGHT_ENABLED", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.preflight_prompt = str(os.getenv("CLAUDE_CODE_PREFLIGHT_PROMPT", "auth-check")).strip() or "auth-check"

    def execute_plan(
        self,
        plan: Dict[str, Any],
        *,
        request_id: str,
        log_sink: Optional[LogSinkFn] = None,
    ) -> Dict[str, Any]:
        started = time.time()
        diagnostics = self._runtime_diagnostics()
        command = self._build_command(plan)
        cwd = self._resolve_cwd(plan)

        if callable(log_sink):
            log_sink(f"[EXEC] running Claude Code CLI in {cwd}")
            log_sink(f"[EXEC] command: {' '.join(command)}")
            for line in diagnostics:
                log_sink(line)

        if self.preflight_enabled:
            preflight_command = self._build_command(plan, prompt_override=self.preflight_prompt)
            preflight = self._run_process(
                preflight_command,
                cwd=cwd,
                timeout_seconds=min(self.default_timeout_seconds, 120),
                log_sink=log_sink,
            )
            if preflight["exit_code"] != 0:
                elapsed_ms = int((time.time() - started) * 1000)
                hints = self._auth_failure_hints(preflight["logs"])
                all_logs = diagnostics + ["[EXEC ERROR] preflight failed"] + preflight["logs"] + hints
                self._persist_run_artifacts(
                    request_id=request_id,
                    status="implementation_failed",
                    summary="Claude Code CLI preflight failed.",
                    elapsed_ms=elapsed_ms,
                    command=preflight_command,
                    cwd=cwd,
                    logs=all_logs,
                    exit_code=preflight["exit_code"],
                )
                return {
                    "branch_name": None,
                    "build_logs": "\n".join(all_logs).strip() or None,
                    "status": "implementation_failed",
                    "final_summary": "Claude Code CLI preflight failed.",
                    "exit_code": preflight["exit_code"],
                }

        run_output = self._run_process(command, cwd=cwd, timeout_seconds=self.default_timeout_seconds, log_sink=log_sink)
        elapsed_ms = int((time.time() - started) * 1000)
        status = "completed" if run_output["exit_code"] == 0 else "implementation_failed"
        summary = "Claude Code CLI completed successfully." if status == "completed" else "Claude Code CLI failed."
        logs_with_diagnostics = diagnostics + run_output["logs"]
        if status != "completed":
            logs_with_diagnostics += self._auth_failure_hints(run_output["logs"])

        self._persist_run_artifacts(
            request_id=request_id,
            status=status,
            summary=summary,
            elapsed_ms=elapsed_ms,
            command=command,
            cwd=cwd,
            logs=logs_with_diagnostics,
            exit_code=run_output["exit_code"],
        )

        return {
            "branch_name": None,
            "build_logs": "\n".join(logs_with_diagnostics).strip() or None,
            "status": status,
            "final_summary": summary,
            "exit_code": run_output["exit_code"],
        }

    def _build_command(self, plan: Dict[str, Any], *, prompt_override: Optional[str] = None) -> List[str]:
        base = str(os.getenv("CLAUDE_CODE_CMD", "claude")).strip() or "claude"
        args_raw = str(os.getenv("CLAUDE_CODE_ARGS", "--print")).strip()
        args = shlex.split(args_raw, posix=os.name != "nt") if args_raw else []
        prompt = prompt_override if prompt_override is not None else self._build_prompt(plan)
        if "{prompt}" in " ".join(args):
            args = [arg.replace("{prompt}", prompt) for arg in args]
            return [base] + args
        return [base] + args + [prompt]

    def _build_prompt(self, plan: Dict[str, Any]) -> str:
        contract = {
            "summary": str(plan.get("summary", "")),
            "project_mode": str(plan.get("project_mode", "")),
            "project_ref": plan.get("project_ref", {}),
            "target_files": plan.get("target_files", []),
            "constraints": plan.get("constraints", []),
            "validation": plan.get("validation", []),
            "product_contract": plan.get("product_contract", {}),
            "review_guidelines": plan.get("review_guidelines", []),
        }
        return (
            "Implement the following PM plan in this repository. "
            "Apply changes directly and run relevant checks.\n\n"
            f"{json.dumps(contract, indent=2)}"
        )

    def _resolve_cwd(self, plan: Dict[str, Any]) -> str:
        project_ref = plan.get("project_ref") if isinstance(plan.get("project_ref"), dict) else {}
        rel_path = str(project_ref.get("path_hint", "")).strip()
        if not rel_path:
            return self.repo_root
        normalized = rel_path.replace("\\", "/").strip("/")
        if normalized.startswith("projects/"):
            normalized = normalized.split("/", 1)[1]
        candidate = Path(self.repo_root) / normalized
        return str(candidate if candidate.exists() else Path(self.repo_root))

    def _run_process(
        self,
        command: List[str],
        *,
        cwd: str,
        timeout_seconds: int,
        log_sink: Optional[LogSinkFn],
    ) -> Dict[str, Any]:
        logs: List[str] = []
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            msg = f"[EXEC ERROR] failed to start Claude Code CLI: {exc}"
            logs.append(msg)
            if callable(log_sink):
                log_sink(msg)
            return {"exit_code": 1, "logs": logs}

        try:
            assert process.stdout is not None
            for line in iter(process.stdout.readline, ""):
                stripped = line.rstrip("\n")
                logs.append(stripped)
                if callable(log_sink):
                    log_sink(stripped)
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            timeout_msg = f"[EXEC ERROR] timed out after {timeout_seconds}s"
            logs.append(timeout_msg)
            if callable(log_sink):
                log_sink(timeout_msg)
            return {"exit_code": 124, "logs": logs}
        return {"exit_code": int(process.returncode or 0), "logs": logs}

    def _runtime_diagnostics(self) -> List[str]:
        cmd = str(os.getenv("CLAUDE_CODE_CMD", "claude")).strip() or "claude"
        return [
            f"[EXEC DIAG] python_executable={os.sys.executable}",
            f"[EXEC DIAG] claude_cmd={cmd}",
            f"[EXEC DIAG] claude_resolved_path={shutil.which(cmd) or 'NOT_FOUND'}",
            f"[EXEC DIAG] HOME={os.getenv('HOME', '')}",
            f"[EXEC DIAG] USERPROFILE={os.getenv('USERPROFILE', '')}",
            f"[EXEC DIAG] CLAUDE_CODE_ARGS={os.getenv('CLAUDE_CODE_ARGS', '--print')}",
        ]

    def _auth_failure_hints(self, logs: List[str]) -> List[str]:
        merged = "\n".join(logs).lower()
        if "oauth token has expired" in merged or "authentication_error" in merged:
            return [
                "[EXEC HINT] Claude auth failed in non-interactive mode.",
                "[EXEC HINT] Run `claude login` in the same shell/environment running orchestrator.",
                "[EXEC HINT] Verify `claude --print \"ping\"` works in that same shell before retrying.",
            ]
        return []

    def _persist_run_artifacts(
        self,
        *,
        request_id: str,
        status: str,
        summary: str,
        elapsed_ms: int,
        command: List[str],
        cwd: str,
        logs: List[str],
        exit_code: int,
    ) -> None:
        run_dir = Path(self.repo_root) / ".orchestrator" / "runs" / request_id
        run_dir.mkdir(parents=True, exist_ok=True)
        summary_payload = {
            "request_id": request_id,
            "status": status,
            "summary": summary,
            "elapsed_ms": elapsed_ms,
            "exit_code": exit_code,
            "cwd": cwd,
            "command": command,
        }
        (run_dir / "summary.json").write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
        (run_dir / "cli_output.log").write_text("\n".join(logs), encoding="utf-8")

