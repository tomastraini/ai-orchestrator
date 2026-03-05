from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from shared.artifact_schemas import validate_artifact, with_artifact_header, write_json_artifact
from shared.event_schemas import append_event, build_event, validate_event
from shared.role_policy_schemas import decide_tool_access
from shared.stage_contracts import RoleName, StageName, validate_stage_sequence


LogSinkFn = Callable[[str], None]


class OpenHandsRuntime:
    def __init__(self, repo_root: str) -> None:
        self.repo_root = repo_root
        self.default_timeout_seconds = int(os.getenv("OPENHANDS_TIMEOUT_SECONDS", "1800"))
        self.enable_specialists = str(os.getenv("OPENHANDS_ENABLE_SPECIALISTS", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def execute_plan(
        self,
        plan: Dict[str, Any],
        *,
        request_id: str,
        log_sink: Optional[LogSinkFn] = None,
    ) -> Dict[str, Any]:
        started = time.time()
        correlation_id = str(uuid.uuid4())
        cwd = self._resolve_cwd(plan)
        run_dir = Path(self.repo_root) / ".orchestrator" / "runs" / request_id
        ai_dir = Path(self.repo_root) / ".ai"
        worklog_path = ai_dir / "worklog.jsonl"
        logs: List[str] = []
        emitted_stages: List[str] = []

        def _emit(stage: StageName, role: RoleName, decision: str, details: Optional[Dict[str, Any]] = None) -> None:
            event = build_event(
                request_id=request_id,
                correlation_id=correlation_id,
                stage=stage.value,
                role=role.value,
                decision=decision,
                details=details or {},
            )
            errors = validate_event(event)
            if errors:
                raise RuntimeError(f"invalid event contract: {'; '.join(errors)}")
            append_event(worklog_path, event)
            emitted_stages.append(stage.value)

        _emit(StageName.PLAN_INGESTED, RoleName.PM, "allow")
        _emit(StageName.DISPATCH_STARTED, RoleName.PLANNER, "allow")

        for role, stage in [
            (RoleName.PLANNER, StageName.PLANNER_COMPLETED),
            (RoleName.BUILDER, StageName.BUILDER_COMPLETED),
            (RoleName.VALIDATOR, StageName.VALIDATOR_COMPLETED),
            (RoleName.FINALIZER, StageName.FINALIZER_COMPLETED),
        ]:
            result = self._run_role(role=role, plan=plan, cwd=cwd, log_sink=log_sink)
            logs.extend(result["logs"])
            if result["exit_code"] != 0:
                # Specialist pass for validator failures.
                if role == RoleName.VALIDATOR and self.enable_specialists:
                    specialist_result = self._run_specialists(plan=plan, cwd=cwd, log_sink=log_sink)
                    logs.extend(specialist_result["logs"])
                    if specialist_result["exit_code"] == 0:
                        result = self._run_role(role=role, plan=plan, cwd=cwd, log_sink=log_sink)
                        logs.extend(result["logs"])
                if result["exit_code"] != 0:
                    elapsed_ms = int((time.time() - started) * 1000)
                    return self._finalize_failure(
                        request_id=request_id,
                        correlation_id=correlation_id,
                        run_dir=run_dir,
                        ai_dir=ai_dir,
                        plan=plan,
                        cwd=cwd,
                        logs=logs,
                        elapsed_ms=elapsed_ms,
                        emitted_stages=emitted_stages,
                        failed_role=role.value,
                    )
            _emit(stage, role, "allow")

        _emit(StageName.EVIDENCE_PUBLISHED, RoleName.FINALIZER, "allow")
        _emit(StageName.RUN_COMPLETED, RoleName.FINALIZER, "allow")
        stage_errors = validate_stage_sequence(emitted_stages)
        if stage_errors:
            elapsed_ms = int((time.time() - started) * 1000)
            logs.extend([f"[CONTRACT ERROR] {err}" for err in stage_errors])
            return self._finalize_failure(
                request_id=request_id,
                correlation_id=correlation_id,
                run_dir=run_dir,
                ai_dir=ai_dir,
                plan=plan,
                cwd=cwd,
                logs=logs,
                elapsed_ms=elapsed_ms,
                emitted_stages=emitted_stages,
                failed_role=RoleName.FINALIZER.value,
            )

        elapsed_ms = int((time.time() - started) * 1000)
        self._write_artifacts(
            request_id=request_id,
            correlation_id=correlation_id,
            run_dir=run_dir,
            ai_dir=ai_dir,
            plan=plan,
            cwd=cwd,
            logs=logs,
            elapsed_ms=elapsed_ms,
            status="completed",
            emitted_stages=emitted_stages,
        )
        return {
            "branch_name": None,
            "build_logs": "\n".join(logs).strip() or None,
            "status": "completed",
            "final_summary": "OpenHands minion loop completed successfully.",
            "exit_code": 0,
        }

    def _run_role(
        self,
        *,
        role: RoleName,
        plan: Dict[str, Any],
        cwd: str,
        log_sink: Optional[LogSinkFn],
    ) -> Dict[str, Any]:
        capability = "mcp_limited" if role in {RoleName.PLANNER, RoleName.FINALIZER} else "mcp"
        policy = decide_tool_access(role.value, capability)
        if not policy.allowed:
            denied = f"[POLICY] role={role.value} capability={capability} denied"
            if callable(log_sink):
                log_sink(denied)
            return {"exit_code": 1, "logs": [denied]}
        command = self._build_command(role=role, plan=plan)
        if callable(log_sink):
            log_sink(f"[OPENHANDS] role={role.value} command={' '.join(command)}")
        return self._run_process(command, cwd=cwd, timeout_seconds=self.default_timeout_seconds, log_sink=log_sink)

    def _run_specialists(
        self,
        *,
        plan: Dict[str, Any],
        cwd: str,
        log_sink: Optional[LogSinkFn],
    ) -> Dict[str, Any]:
        aggregate_logs: List[str] = []
        for role in [RoleName.TEST_FIXER, RoleName.DEPENDENCY_FIXER]:
            result = self._run_role(role=role, plan=plan, cwd=cwd, log_sink=log_sink)
            aggregate_logs.extend(result["logs"])
            if result["exit_code"] != 0:
                return {"exit_code": result["exit_code"], "logs": aggregate_logs}
        return {"exit_code": 0, "logs": aggregate_logs}

    def _build_command(self, *, role: RoleName, plan: Dict[str, Any]) -> List[str]:
        base = str(os.getenv("OPENHANDS_CMD", "openhands")).strip() or "openhands"
        args_raw = str(os.getenv("OPENHANDS_ARGS", "run")).strip()
        args = shlex.split(args_raw, posix=os.name != "nt") if args_raw else []
        prompt = self._build_prompt(role=role, plan=plan)
        if "{prompt}" in " ".join(args):
            args = [arg.replace("{prompt}", prompt) for arg in args]
            return [base] + args
        return [base] + args + [prompt]

    def _build_prompt(self, *, role: RoleName, plan: Dict[str, Any]) -> str:
        contract = {
            "role": role.value,
            "summary": str(plan.get("summary", "")),
            "target_files": plan.get("target_files", []),
            "validation": plan.get("validation", []),
            "constraints": plan.get("constraints", []),
        }
        return f"Execute role stage for orchestrator contract:\n{json.dumps(contract, indent=2)}"

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
        resolved = shutil.which(command[0]) or "NOT_FOUND"
        logs.append(f"[OPENHANDS DIAG] cmd={command[0]} resolved={resolved}")
        if callable(log_sink):
            log_sink(logs[-1])
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
            msg = f"[OPENHANDS ERROR] failed to start: {exc}"
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
            timeout_msg = f"[OPENHANDS ERROR] timed out after {timeout_seconds}s"
            logs.append(timeout_msg)
            if callable(log_sink):
                log_sink(timeout_msg)
            return {"exit_code": 124, "logs": logs}
        return {"exit_code": int(process.returncode or 0), "logs": logs}

    def _write_artifacts(
        self,
        *,
        request_id: str,
        correlation_id: str,
        run_dir: Path,
        ai_dir: Path,
        plan: Dict[str, Any],
        cwd: str,
        logs: List[str],
        elapsed_ms: int,
        status: str,
        emitted_stages: List[str],
    ) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(
            run_dir / "summary.json",
            {
                "request_id": request_id,
                "status": status,
                "summary": "OpenHands dispatcher execution completed." if status == "completed" else "OpenHands dispatcher failed.",
                "elapsed_ms": elapsed_ms,
                "cwd": cwd,
                "stages": emitted_stages,
            },
        )
        (run_dir / "cli_output.log").write_text("\n".join(logs), encoding="utf-8")

        spec_payload = with_artifact_header(
            {"requirement_summary": str(plan.get("summary", ""))},
            request_id=request_id,
            correlation_id=correlation_id,
        )
        plan_payload = with_artifact_header(
            {"plan": plan},
            request_id=request_id,
            correlation_id=correlation_id,
        )
        audit_payload = with_artifact_header(
            {
                "status": status,
                "stages": emitted_stages,
                "decisions": len(emitted_stages),
            },
            request_id=request_id,
            correlation_id=correlation_id,
        )
        for payload in [spec_payload, plan_payload, audit_payload]:
            errors = validate_artifact(payload)
            if errors:
                raise RuntimeError(f"invalid artifact contract: {'; '.join(errors)}")
        write_json_artifact(ai_dir / "spec.json", spec_payload)
        write_json_artifact(ai_dir / "plan.json", plan_payload)
        write_json_artifact(ai_dir / "audit.json", audit_payload)
        (ai_dir / "final-report.md").write_text(
            f"# Final Report\n\n- request_id: {request_id}\n- status: {status}\n- elapsed_ms: {elapsed_ms}\n",
            encoding="utf-8",
        )
        (ai_dir / "evidence").mkdir(parents=True, exist_ok=True)

    def _finalize_failure(
        self,
        *,
        request_id: str,
        correlation_id: str,
        run_dir: Path,
        ai_dir: Path,
        plan: Dict[str, Any],
        cwd: str,
        logs: List[str],
        elapsed_ms: int,
        emitted_stages: List[str],
        failed_role: str,
    ) -> Dict[str, Any]:
        logs.append(f"[OPENHANDS ERROR] execution failed at role={failed_role}")
        self._write_artifacts(
            request_id=request_id,
            correlation_id=correlation_id,
            run_dir=run_dir,
            ai_dir=ai_dir,
            plan=plan,
            cwd=cwd,
            logs=logs,
            elapsed_ms=elapsed_ms,
            status="implementation_failed",
            emitted_stages=emitted_stages,
        )
        return {
            "branch_name": None,
            "build_logs": "\n".join(logs).strip() or None,
            "status": "implementation_failed",
            "final_summary": "OpenHands dispatcher failed.",
            "exit_code": 1,
        }
