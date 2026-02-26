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
from services.dev.types.dev_graph_state import DevGraphState
from services.dev.edit_primitives import patch_region, rename_path
from services.workspace.cognition.scaffold_probe import probe_scaffold_layout
from services.workspace.cognition.snapshot_store import persist_cognition_snapshot
from services.workspace.project_index import build_cognition_index, detect_stack_from_markers, rank_candidate_files, scan_workspace_context
from shared.dev_schemas import DevChecklistItem, DevTask, derive_project_name
from shared.pathing import canonicalize_scope_path


DevAskFn = Callable[[str], str]
LLMCorrectorFn = Callable[[Dict[str, Any]], str]


class DevMasterGraph:
    MEMORY_LIMITS = {
        "files_inspected": 200,
        "symbols_discovered": 300,
        "assumptions": 120,
        "candidate_attempts": 240,
        "candidate_rejections": 240,
        "correction_attempts": 120,
        "command_failures": 160,
        "diagnostic_file_refs": 120,
        "validation_inference": 120,
        "attempted_commands": 100,
        "errors": 100,
        "touched_paths": 200,
    }
    VALIDATION_COMMAND_PREFIXES = (
        "npm ",
        "pnpm ",
        "yarn ",
        "python ",
        "pytest",
        "dotnet ",
        "bundle ",
        "rake ",
        "make ",
        "./",
        "bash ",
        "sh ",
    )
    PROJECT_MARKER_FILES = (
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Pipfile",
        "Gemfile",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "composer.json",
    )
    SOURCE_DIR_HINTS = ("src", "app", "lib")
    INDEX_IGNORE_DIRS = {"node_modules", ".git", ".venv", "__pycache__", "dist", "build", ".next", ".cache"}

    def __init__(self) -> None:
        graph = StateGraph(DevGraphState)
        graph.add_node("ingest_pm_plan", self._ingest_pm_plan)
        graph.add_node("derive_dev_todos", self._derive_dev_todos)
        graph.add_node("dev_preflight_planning", self._dev_preflight_planning)
        graph.add_node("ask_cli_clarifications_if_needed", self._ask_cli_clarifications_if_needed)
        graph.add_node("prepare_execution_steps", self._prepare_execution_steps)
        graph.add_node("execute_bootstrap_phase", self._execute_bootstrap_phase)
        graph.add_node("execute_implementation_phase", self._execute_implementation_phase)
        graph.add_node("execute_validation_phase", self._execute_validation_phase)
        graph.add_node("execute_final_compile_gate", self._execute_final_compile_gate)
        graph.add_node("finalize_result", self._finalize_result)

        graph.add_edge(START, "ingest_pm_plan")
        graph.add_edge("ingest_pm_plan", "derive_dev_todos")
        graph.add_edge("derive_dev_todos", "dev_preflight_planning")
        graph.add_edge("dev_preflight_planning", "ask_cli_clarifications_if_needed")
        graph.add_edge("ask_cli_clarifications_if_needed", "prepare_execution_steps")
        graph.add_edge("prepare_execution_steps", "execute_bootstrap_phase")
        graph.add_edge("execute_bootstrap_phase", "execute_implementation_phase")
        graph.add_edge("execute_implementation_phase", "execute_validation_phase")
        graph.add_edge("execute_validation_phase", "execute_final_compile_gate")
        graph.add_edge("execute_final_compile_gate", "finalize_result")
        graph.add_edge("finalize_result", END)
        self._compiled_graph = graph.compile()

    def run(
        self,
        *,
        request_id: str,
        plan: Dict[str, Any],
        scope_root: str,
        ask_user: DevAskFn | None = None,
        handoff: Dict[str, Any] | None = None,
        llm_corrector: LLMCorrectorFn | None = None,
        max_model_calls_per_run: int = 1,
        log_sink: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        initial_state: DevGraphState = {
            "request_id": request_id,
            "plan": plan,
            "handoff": handoff or {},
            "scope_root": scope_root,
            "status": "running",
            "current_step": "init",
            "bootstrap_tasks": [],
            "implementation_targets": [],
            "validation_tasks": [],
            "clarifications": [],
            "logs": [],
            "touched_paths": [],
            "errors": [],
            "ask_user": ask_user,
            "llm_corrector": llm_corrector,
            "retry_count": 0,
            "max_retries": 5,
            "last_error": "",
            "attempt_history": [],
            "bootstrap_status": "pending",
            "implementation_status": "pending",
            "validation_status": "pending",
            "final_compile_status": "pending",
            "active_project_root": "",
            "detected_stacks": [],
            "dev_preflight_plan": {},
            "final_compile_tasks": [],
            "internal_checklist": [],
            "checklist_index": {},
            "task_outcomes": [],
            "telemetry_events": [],
            "dev_discovery_candidates": [],
            "dev_technical_plan": {},
            "dev_plan_approved": False,
            "repository_memory": (
                dict(handoff.get("memory", {}))
                if isinstance(handoff, dict) and isinstance(handoff.get("memory"), dict)
                else DevMasterGraph._default_repository_memory()
            ),
            "checklist_cursor": "",
            "llm_calls_used": 0,
            "llm_call_budget": max(0, int(max_model_calls_per_run)),
            "phase_status": {
                "ingest_pm_plan": "pending",
                "derive_dev_todos": "pending",
                "dev_preflight_planning": "pending",
                "ask_cli_clarifications_if_needed": "pending",
                "prepare_execution_steps": "pending",
                "execute_bootstrap_phase": "pending",
                "execute_implementation_phase": "pending",
                "execute_validation_phase": "pending",
                "execute_final_compile_gate": "pending",
                "finalize_result": "pending",
            },
            "implementation_pass_statuses": [],
            "log_sink": log_sink,
            "root_resolution_evidence": {},
            "active_root_file_index": {},
            "llm_context_contract": {},
            "target_resolution_evidence": {},
            "capability_gaps": [],
            "reliability_metrics": {},
        }
        initial_state["repository_memory"] = DevMasterGraph._trim_repository_memory(
            {
                **DevMasterGraph._default_repository_memory(),
                **(
                    initial_state.get("repository_memory", {})
                    if isinstance(initial_state.get("repository_memory"), dict)
                    else {}
                ),
            }
        )
        result = self._compiled_graph.invoke(initial_state)
        result.pop("ask_user", None)
        result.pop("llm_corrector", None)
        result.pop("log_sink", None)
        return result

    @staticmethod
    def _emit(state: DevGraphState, message: str) -> None:
        state["logs"].append(message)
        sink = state.get("log_sink")
        if callable(sink):
            try:
                sink(message)
            except Exception:
                pass

    @staticmethod
    def _sanitize_text(value: Any, max_length: int = 600) -> str:
        text = str(value or "")
        if len(text) > max_length:
            text = f"{text[:max_length]}... [truncated {len(text) - max_length} chars]"
        patterns = [
            re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
            re.compile(r"(token\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
            re.compile(r"(password\s*[:=]\s*)([^\s\"']+)", re.IGNORECASE),
        ]
        for pattern in patterns:
            text = pattern.sub(r"\1[REDACTED]", text)
        return text

    @staticmethod
    def _relpath_safe(state: DevGraphState, path: str) -> str:
        try:
            scope = os.path.abspath(str(state.get("scope_root", "")))
            candidate = os.path.abspath(path)
            if scope and os.path.commonpath([scope, candidate]) == scope:
                return os.path.relpath(candidate, scope).replace("\\", "/")
            return candidate.replace("\\", "/")
        except Exception:
            return str(path).replace("\\", "/")

    @staticmethod
    def _emit_event(state: DevGraphState, category: str, **metadata: Any) -> None:
        event = {
            "timestamp_ms": int(time.time() * 1000),
            "request_id": str(state.get("request_id", "")),
            "phase": str(state.get("current_step", "")),
            "category": category,
            "metadata": metadata,
        }
        state.setdefault("telemetry_events", []).append(event)
        DevMasterGraph._emit(state, f"[EVENT] {json.dumps(event, sort_keys=True)}")

    @staticmethod
    def _default_repository_memory() -> Dict[str, Any]:
        return {
            "files_inspected": [],
            "symbols_discovered": [],
            "assumptions": [],
            "candidate_attempts": [],
            "candidate_rejections": [],
            "correction_attempts": [],
            "command_failures": [],
            "diagnostic_file_refs": [],
            "validation_inference": [],
            "attempted_commands": [],
            "errors": [],
            "touched_paths": [],
        }

    @staticmethod
    def _trim_repository_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
        for key, limit in DevMasterGraph.MEMORY_LIMITS.items():
            value = memory.get(key, [])
            if not isinstance(value, list):
                memory[key] = []
                continue
            if len(value) > int(limit):
                memory[key] = value[-int(limit) :]
        return memory

    @staticmethod
    def _ensure_repository_memory(state: DevGraphState) -> Dict[str, Any]:
        current = state.get("repository_memory")
        memory = dict(current) if isinstance(current, dict) else {}
        for key, default_value in DevMasterGraph._default_repository_memory().items():
            if key not in memory or not isinstance(memory.get(key), list):
                memory[key] = list(default_value)
        state["repository_memory"] = DevMasterGraph._trim_repository_memory(memory)
        return state["repository_memory"]

    @staticmethod
    def _remember(state: DevGraphState, bucket: str, payload: Dict[str, Any]) -> None:
        memory = DevMasterGraph._ensure_repository_memory(state)
        entry = {
            "timestamp_ms": int(time.time() * 1000),
            "phase": str(state.get("current_step", "")),
            "kind": bucket,
            "data": payload,
        }
        memory.setdefault(bucket, []).append(entry)
        state["repository_memory"] = DevMasterGraph._trim_repository_memory(memory)

    @staticmethod
    def _remember_text_value(state: DevGraphState, bucket: str, value: str) -> None:
        memory = DevMasterGraph._ensure_repository_memory(state)
        items = memory.setdefault(bucket, [])
        if value and value not in items:
            items.append(value)
        state["repository_memory"] = DevMasterGraph._trim_repository_memory(memory)

    @staticmethod
    def _record_error_file_refs(state: DevGraphState, refs: List[str]) -> None:
        for ref in refs:
            normalized = str(ref or "").replace("\\", "/").strip()
            if not normalized:
                continue
            DevMasterGraph._remember_text_value(state, "diagnostic_file_refs", normalized)

    @staticmethod
    def _has_recent_candidate_rejection(state: DevGraphState, candidate_path: str) -> bool:
        normalized = str(candidate_path or "").replace("\\", "/").strip().casefold()
        memory = DevMasterGraph._ensure_repository_memory(state)
        rejections = memory.get("candidate_rejections", [])
        if not isinstance(rejections, list):
            return False
        for entry in reversed(rejections):
            data = entry.get("data", {}) if isinstance(entry, dict) else {}
            candidate = str(data.get("candidate_path", "")).replace("\\", "/").strip().casefold()
            if candidate and candidate == normalized:
                return True
        return False

    @staticmethod
    def _reindex_checklist(state: DevGraphState) -> None:
        checklist = state.get("internal_checklist", [])
        state["checklist_index"] = {
            str(item.get("id", "")): idx
            for idx, item in enumerate(checklist)
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }

    @staticmethod
    def _upsert_checklist_item(state: DevGraphState, item: DevChecklistItem) -> None:
        checklist = state.get("internal_checklist", [])
        index = state.get("checklist_index", {})
        payload = asdict(item)
        item_id = item.id
        if item_id in index:
            checklist[index[item_id]] = payload
        else:
            checklist.append(payload)
        state["internal_checklist"] = checklist
        DevMasterGraph._reindex_checklist(state)

    @staticmethod
    def _find_checklist_item(state: DevGraphState, item_id: str) -> Optional[Dict[str, Any]]:
        idx = state.get("checklist_index", {}).get(item_id)
        if idx is None:
            return None
        checklist = state.get("internal_checklist", [])
        if idx < 0 or idx >= len(checklist):
            return None
        item = checklist[idx]
        return item if isinstance(item, dict) else None

    @staticmethod
    def _append_item_evidence(item: Dict[str, Any], evidence: Optional[Dict[str, Any]]) -> None:
        if not evidence:
            return
        current = item.get("evidence")
        if not isinstance(current, list):
            current = []
        current.append(evidence)
        item["evidence"] = current

    @staticmethod
    def _set_checklist_status(
        state: DevGraphState,
        item_id: str,
        status: str,
        *,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        item = DevMasterGraph._find_checklist_item(state, item_id)
        if not item:
            return
        item["status"] = status
        DevMasterGraph._append_item_evidence(item, evidence)
        state["checklist_cursor"] = item_id
        DevMasterGraph._emit(
            state,
            f"[CHECKLIST] item={item_id} status={status}",
        )
        DevMasterGraph._emit_event(
            state,
            "checklist_outcome",
            item_id=item_id,
            status=status,
            evidence=evidence or {},
        )

    @staticmethod
    def _next_actionable_checklist_item(state: DevGraphState) -> Optional[Dict[str, Any]]:
        checklist = state.get("internal_checklist", [])
        completed = {
            str(item.get("id", ""))
            for item in checklist
            if isinstance(item, dict) and str(item.get("status", "")) == "completed"
        }
        for item in checklist:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "pending"))
            if status in {"completed", "failed"}:
                continue
            deps = item.get("depends_on", [])
            if isinstance(deps, list) and any(str(dep) not in completed for dep in deps):
                continue
            return item
        return None

    @staticmethod
    def _all_mandatory_checklist_items_completed(state: DevGraphState) -> bool:
        for item in state.get("internal_checklist", []):
            if not isinstance(item, dict):
                continue
            if not bool(item.get("mandatory", True)):
                continue
            if str(item.get("status", "")) != "completed":
                return False
        return True

    @staticmethod
    def _infer_bootstrap_tasks_from_intent(state: DevGraphState) -> List[DevTask]:
        # Runtime policy: do not enforce a prescribed scaffolding workflow.
        # DEV may still execute explicit commands from DEV-authored handoffs,
        # but PM intent alone should not auto-expand into fixed scaffold recipes.
        _ = state
        return []

    @staticmethod
    def _read_package_scripts(project_dir: str) -> Dict[str, str]:
        package_json = os.path.join(project_dir, "package.json")
        if not os.path.exists(package_json):
            return {}
        try:
            with open(package_json, "r", encoding="utf-8", errors="ignore") as fh:
                payload = json.load(fh)
        except Exception:
            return {}
        scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
        if not isinstance(scripts, dict):
            return {}
        normalized: Dict[str, str] = {}
        for key, value in scripts.items():
            k = str(key).strip()
            v = str(value).strip()
            if k and v:
                normalized[k] = v
        return normalized

    @staticmethod
    def _infer_node_runner(project_dir: str) -> str:
        if os.path.exists(os.path.join(project_dir, "pnpm-lock.yaml")):
            return "pnpm"
        if os.path.exists(os.path.join(project_dir, "yarn.lock")):
            return "yarn"
        return "npm"

    @staticmethod
    def _discover_repository_command_candidates(project_dir: str) -> Dict[str, List[str]]:
        candidates: Dict[str, List[str]] = {"build": [], "test": [], "lint": [], "check": [], "setup": []}

        def _add(kind: str, command: str) -> None:
            cmd = str(command or "").strip()
            if not cmd:
                return
            bucket = candidates.setdefault(kind, [])
            if cmd not in bucket:
                bucket.append(cmd)

        scripts = DevMasterGraph._read_package_scripts(project_dir)
        if scripts:
            runner = DevMasterGraph._infer_node_runner(project_dir)
            for script_name in scripts:
                if script_name in {"build", "compile"}:
                    _add("build", f"{runner} run {script_name}")
                if script_name in {"test", "unit", "integration"} or script_name.startswith("test:"):
                    _add("test", f"{runner} run {script_name}")
                if script_name in {"lint", "eslint"} or script_name.startswith("lint:"):
                    _add("lint", f"{runner} run {script_name}")
                if script_name in {"check", "typecheck"} or script_name.startswith("check:"):
                    _add("check", f"{runner} run {script_name}")
            _add("setup", f"{runner} install")

        if os.path.exists(os.path.join(project_dir, "Makefile")):
            try:
                with open(os.path.join(project_dir, "Makefile"), "r", encoding="utf-8", errors="ignore") as fh:
                    blob = fh.read().lower()
                if re.search(r"^build\s*:", blob, flags=re.MULTILINE):
                    _add("build", "make build")
                if re.search(r"^test\s*:", blob, flags=re.MULTILINE):
                    _add("test", "make test")
                if re.search(r"^lint\s*:", blob, flags=re.MULTILINE):
                    _add("lint", "make lint")
            except Exception:
                pass

        entries: List[str] = []
        try:
            entries = os.listdir(project_dir)
        except Exception:
            entries = []

        if any(str(x).endswith((".sln", ".csproj")) for x in entries):
            _add("setup", "dotnet restore")
            _add("build", "dotnet build")
            _add("test", "dotnet test")
        if os.path.exists(os.path.join(project_dir, "Cargo.toml")):
            _add("build", "cargo build")
            _add("test", "cargo test")
        if os.path.exists(os.path.join(project_dir, "go.mod")):
            _add("build", "go build ./...")
            _add("test", "go test ./...")
        if os.path.exists(os.path.join(project_dir, "gradlew")):
            _add("build", "./gradlew build")
            _add("test", "./gradlew test")
        elif os.path.exists(os.path.join(project_dir, "build.gradle")) or os.path.exists(os.path.join(project_dir, "build.gradle.kts")):
            _add("build", "gradle build")
            _add("test", "gradle test")
        if os.path.exists(os.path.join(project_dir, "pyproject.toml")) or os.path.exists(os.path.join(project_dir, "requirements.txt")):
            _add("test", "python -m pytest")
            if os.path.exists(os.path.join(project_dir, "requirements.txt")):
                _add("setup", "python -m pip install -r requirements.txt")

        return candidates

    @staticmethod
    def _is_validation_command_executable(command: str, *, project_dir: str) -> Tuple[bool, str]:
        cmd = str(command or "").strip()
        low = cmd.lower()
        scripts = DevMasterGraph._read_package_scripts(project_dir)
        if low.startswith("npm run ") or low.startswith("pnpm run ") or low.startswith("yarn run "):
            parts = cmd.split()
            script_name = parts[2] if len(parts) >= 3 else ""
            if not os.path.exists(os.path.join(project_dir, "package.json")):
                return False, "missing_package_json"
            if script_name and script_name not in scripts:
                return False, f"missing_script:{script_name}"
            return True, "ok"
        if low.startswith("npm test") or low.startswith("pnpm test") or low.startswith("yarn test"):
            if not os.path.exists(os.path.join(project_dir, "package.json")):
                return False, "missing_package_json"
            if "test" not in scripts:
                return False, "missing_script:test"
            return True, "ok"
        if low.startswith("npm install") or low.startswith("pnpm install") or low.startswith("yarn install"):
            has_package = os.path.exists(os.path.join(project_dir, "package.json"))
            return has_package, "ok" if has_package else "missing_package_json"
        if low.startswith("make "):
            return os.path.exists(os.path.join(project_dir, "Makefile")), "missing_makefile"
        if low.startswith("./"):
            executable = cmd.split()[0]
            if os.path.exists(os.path.join(project_dir, executable[2:])):
                return True, "ok"
            return False, f"missing_file:{executable}"
        first_token = cmd.split()[0] if cmd.split() else ""
        if first_token:
            if shutil.which(first_token) is not None:
                return True, "ok"
            if os.path.exists(os.path.join(project_dir, first_token)):
                return True, "ok"
            if first_token in {"python", "python3"} and shutil.which("python") is not None:
                return True, "ok"
            return False, f"missing_capability:{first_token}"
        return True, "ok"

    @staticmethod
    def _bootstrap_artifact_gate(state: DevGraphState, *, task: DevTask) -> Tuple[bool, Dict[str, Any]]:
        active_root = str(state.get("active_project_root", "")).strip()
        if not active_root:
            project_root = str(state.get("project_root", ""))
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            active_root = os.path.join(state.get("scope_root", ""), rel)
        marker_files = [
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "Gemfile",
            "go.mod",
            "Cargo.toml",
            "pom.xml",
        ]
        present_markers = [name for name in marker_files if os.path.exists(os.path.join(active_root, name))]
        command = str(task.command or "").lower()
        stdout_blob = "\n".join(
            [str(x.get("stdout_excerpt", "")) for x in state.get("task_outcomes", []) if isinstance(x, dict) and x.get("task_id") == task.id]
        ).lower()
        cancellation_signatures = ["operation cancelled", "operation canceled", "aborted", "cancelled"]
        cancelled = any(sig in stdout_blob for sig in cancellation_signatures)
        is_scaffold_like = any(tok in command for tok in [" create ", " create-", " init ", " new "])
        if cancelled:
            return False, {"reason": "cancellation_signature_detected", "task_id": task.id}
        if is_scaffold_like and not present_markers:
            return False, {
                "reason": "bootstrap_incomplete",
                "task_id": task.id,
                "active_root": DevMasterGraph._relpath_safe(state, active_root),
                "markers_found": present_markers,
            }
        return True, {
            "task_id": task.id,
            "active_root": DevMasterGraph._relpath_safe(state, active_root),
            "markers_found": present_markers,
        }

    @staticmethod
    def _recovery_satisfies_task_intent(task_kind: str, failed_command: str, recovered_command: str) -> bool:
        failed = str(failed_command or "").lower()
        recovered = str(recovered_command or "").lower()
        if not recovered:
            return False
        if any(tok in failed for tok in ["install", "npm i", "pnpm i", "yarn install"]) and not any(
            tok in recovered for tok in ["install", "npm i", "pnpm i", "yarn install"]
        ):
            return False
        if any(tok in failed for tok in ["create", "init", "scaffold", "new"]) and not any(
            tok in recovered for tok in ["create", "init", "scaffold", "new"]
        ):
            return False
        if task_kind == "bootstrap" and any(tok in recovered for tok in ["find ", "ls ", "dir ", "rg ", "where "]):
            return False
        return True

    @staticmethod
    def _extract_deterministic_failure_signatures(attempt_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        signatures: Dict[str, Dict[str, Any]] = {}
        for attempt in attempt_history:
            if not isinstance(attempt, dict):
                continue
            command = str(attempt.get("command", "")).strip()
            category = str(attempt.get("category", "unknown")).strip() or "unknown"
            stderr = str(attempt.get("stderr", "")).lower()
            stdout = str(attempt.get("stdout", "")).lower()
            if "operation cancelled" in stderr or "operation cancelled" in stdout:
                signature = "operation_cancelled_scaffold"
                decision_point = "bootstrap_artifact_gate"
            elif "not found" in stderr and ("sh: " in stderr or "command not found" in stderr):
                signature = "tool_missing_on_runtime"
                decision_point = "capability_gate"
            elif category in {"timeout"}:
                signature = "timeout_without_readiness"
                decision_point = "retry_policy"
            else:
                signature = f"{category}:{command[:80].lower()}"
                decision_point = "generic_error_handling"
            key = f"{signature}::{decision_point}"
            entry = signatures.setdefault(
                key,
                {
                    "signature": signature,
                    "decision_point": decision_point,
                    "count": 0,
                    "sample_command": command,
                    "category": category,
                },
            )
            entry["count"] = int(entry.get("count", 0)) + 1
        return sorted(signatures.values(), key=lambda x: int(x.get("count", 0)), reverse=True)

    @staticmethod
    def _terminal_failure_gate(state: DevGraphState) -> Dict[str, Any]:
        errors_blob = "\n".join([str(x) for x in state.get("errors", [])]).lower()
        attempts = [x for x in state.get("attempt_history", []) if isinstance(x, dict)]
        signatures: Dict[str, int] = {}
        for attempt in attempts:
            key = f"{attempt.get('task_id','')}::{attempt.get('category','')}::{str(attempt.get('stderr',''))[:120]}"
            signatures[key] = signatures.get(key, 0) + 1
        criteria = {
            "integrity_compromised": any(
                tok in errors_blob for tok in ["escapes scope", "policy violation", "unsafe", "blocked command"]
            ),
            "llm_budget_exhausted": "llm model-call budget reached" in errors_blob or "llm budget" in errors_blob,
        }
        approved = bool(criteria["integrity_compromised"] or criteria["llm_budget_exhausted"])
        criterion = (
            "integrity_compromised"
            if criteria["integrity_compromised"]
            else ("llm_budget_exhausted" if criteria["llm_budget_exhausted"] else "none")
        )
        return {"approved": approved, "criterion": criterion, "criteria": criteria, "attempt_signatures": signatures}

    @staticmethod
    def _persist_run_artifacts(state: DevGraphState) -> None:
        scope_root = str(state.get("scope_root", ""))
        request_id = str(state.get("request_id", "")).strip() or "unknown"
        run_dir = os.path.join(scope_root, ".orchestrator", "runs", request_id)
        os.makedirs(run_dir, exist_ok=True)
        reasoning_path = os.path.join(run_dir, "dev_reasoning_checkpoints.json")
        trace_path = os.path.join(run_dir, "dev_execution_trace.txt")
        reasoning = {
            "request_id": request_id,
            "project_root": state.get("project_root", ""),
            "active_project_root": state.get("active_project_root", ""),
            "phase_status": state.get("phase_status", {}),
            "technical_plan": state.get("dev_technical_plan", {}),
            "checklist_cursor": state.get("checklist_cursor", ""),
            "status": state.get("status", ""),
            "errors": state.get("errors", []),
        }
        with open(reasoning_path, "w", encoding="utf-8") as fh:
            json.dump(reasoning, fh, indent=2)
        lines: List[str] = []
        lines.append(f"request_id={request_id}")
        lines.append(f"status={state.get('status','')}")
        lines.append(f"project_root={state.get('project_root','')}")
        lines.append(f"active_project_root={state.get('active_project_root','')}")
        lines.append("")
        lines.append("## Phase Status")
        for key, value in (state.get("phase_status", {}) or {}).items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        lines.append("## Commands / Outcomes")
        for outcome in [x for x in state.get("task_outcomes", []) if isinstance(x, dict)]:
            lines.append(
                f"- task={outcome.get('task_id')} status={outcome.get('status')} "
                f"category={outcome.get('category')} cmd={outcome.get('command')}"
            )
        lines.append("")
        lines.append("## Files Touched")
        for path in [str(x) for x in state.get("touched_paths", []) if isinstance(x, str)]:
            lines.append(f"- {DevMasterGraph._relpath_safe(state, path)}")
        lines.append("")
        lines.append("## Errors")
        for err in [str(x) for x in state.get("errors", []) if isinstance(x, str)]:
            lines.append(f"- {err}")
        lines.append("")
        lines.append("## Timeline Logs")
        for entry in [str(x) for x in state.get("logs", []) if isinstance(x, str)]:
            lines.append(entry)
        with open(trace_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    @staticmethod
    def _build_internal_checklist(state: DevGraphState) -> None:
        handoff = state.get("handoff") or {}
        restored = handoff.get("internal_checklist")
        restored_by_id: Dict[str, Dict[str, Any]] = {}
        if isinstance(restored, list) and restored:
            for item in restored:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id", "")).strip()
                if not item_id:
                    continue
                restored_by_id[item_id] = item

        checklist: List[Dict[str, Any]] = []
        for task in state.get("bootstrap_tasks", []):
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="bootstrap",
                    description=task.description,
                    task_ref=task.id,
                    success_criteria=["command exits with code 0"],
                )
            )
            checklist.append(restored_by_id.get(item_id, default_item))
        for idx, target in enumerate(state.get("implementation_targets", []), start=1):
            file_name = str(target.get("file_name", "")).strip() or f"target_{idx}"
            item_id = f"todo_impl_{idx}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="implementation",
                    description=f"implement {file_name}",
                    target_ref=str(target.get("expected_path_hint", file_name)),
                    success_criteria=["target file mutated with evidence"],
                )
            )
            checklist.append(restored_by_id.get(item_id, default_item))
        for task in state.get("validation_tasks", []):
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="validation",
                    description=task.description,
                    task_ref=task.id,
                    success_criteria=["validation task completed"],
                    mandatory=False,
                )
            )
            checklist.append(restored_by_id.get(item_id, default_item))
        for task in state.get("final_compile_tasks", []):
            deps = [str(item.get("id")) for item in checklist if isinstance(item, dict)]
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="final_compile",
                    description=task.description,
                    task_ref=task.id,
                    depends_on=deps,
                    success_criteria=["compile/build gate command completed"],
                )
            )
            checklist.append(restored_by_id.get(item_id, default_item))
        state["internal_checklist"] = checklist
        DevMasterGraph._reindex_checklist(state)
        if restored_by_id:
            DevMasterGraph._emit(
                state,
                f"[CHECKLIST] restored_and_reconciled items={len(checklist)} restored={len(restored_by_id)}",
            )
        else:
            DevMasterGraph._emit(state, f"[CHECKLIST] initialized items={len(checklist)}")

    @staticmethod
    def _ingest_pm_plan(state: DevGraphState) -> DevGraphState:
        return ingest_pm_plan_phase(state, DevMasterGraph)

    @staticmethod
    def _derive_dev_todos(state: DevGraphState) -> DevGraphState:
        return derive_dev_todos_phase(state, DevMasterGraph)

    @staticmethod
    def _detect_stacks_for_root(project_dir: str) -> List[str]:
        markers: List[str] = []
        for marker in ["package.json", "pyproject.toml", "requirements.txt", "Gemfile", "Cargo.toml", "go.mod", "pom.xml"]:
            if os.path.exists(os.path.join(project_dir, marker)):
                markers.append(marker)
        top_entries = []
        try:
            top_entries = os.listdir(project_dir)
        except Exception:
            top_entries = []
        if any(x.endswith(".csproj") or x.endswith(".sln") for x in top_entries):
            markers.append("*.csproj")
        stacks = detect_stack_from_markers(markers, top_entries=top_entries)
        return stacks or ["generic"]

    @staticmethod
    def _default_validation_commands(stacks: List[str]) -> List[str]:
        _ = stacks
        return []

    @staticmethod
    def _is_long_running_validation_command(command: str) -> bool:
        low = f" {str(command or '').lower()} "
        hints = [
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
        return any(token in low for token in hints)

    @staticmethod
    def _infer_final_compile_commands(
        *,
        project_dir: str,
        stacks: List[str],
        validation_commands: List[str],
    ) -> List[str]:
        compile_candidates: List[str] = []
        for command in validation_commands:
            if not DevMasterGraph._is_long_running_validation_command(command):
                executable, _reason = DevMasterGraph._is_validation_command_executable(
                    command, project_dir=project_dir
                )
                if executable:
                    compile_candidates.append(command)
        if compile_candidates:
            return compile_candidates

        repository_candidates = DevMasterGraph._discover_repository_command_candidates(project_dir)
        prioritized: List[str] = []
        for key in ["build", "check", "test"]:
            prioritized.extend(repository_candidates.get(key, []))
        for command in prioritized:
            if not DevMasterGraph._is_long_running_validation_command(command):
                executable, _reason = DevMasterGraph._is_validation_command_executable(
                    command, project_dir=project_dir
                )
                if executable:
                    compile_candidates.append(command)

        if not compile_candidates:
            default_candidates = DevMasterGraph._default_validation_commands(stacks)
            for command in default_candidates:
                if not DevMasterGraph._is_long_running_validation_command(command):
                    executable, _reason = DevMasterGraph._is_validation_command_executable(
                        command, project_dir=project_dir
                    )
                    if executable:
                        compile_candidates.append(command)

        return compile_candidates

    @staticmethod
    def _extract_validation_command(
        raw: str,
        *,
        stacks: Optional[List[str]] = None,
        project_dir: str = "",
    ) -> str:
        val = (raw or "").strip()
        if not val:
            return ""
        _ = stacks
        if any(val.startswith(prefix) for prefix in DevMasterGraph.VALIDATION_COMMAND_PREFIXES):
            return val
        # Accept backticked shell snippets from PM text, e.g. "Run `npm run build`".
        backticked = re.findall(r"`([^`]+)`", val)
        for token in backticked:
            normalized = token.strip()
            if any(normalized.startswith(prefix) for prefix in DevMasterGraph.VALIDATION_COMMAND_PREFIXES):
                return normalized
        lowered = val.lower()
        if project_dir:
            repository_candidates = DevMasterGraph._discover_repository_command_candidates(project_dir)
            if any(tok in lowered for tok in ["lint", "style", "format"]):
                for command in repository_candidates.get("lint", []):
                    executable, _ = DevMasterGraph._is_validation_command_executable(command, project_dir=project_dir)
                    if executable:
                        return command
            if any(tok in lowered for tok in ["build", "compile", "compilation", "typecheck", "bundle"]):
                for key in ["build", "check", "test"]:
                    for command in repository_candidates.get(key, []):
                        executable, _ = DevMasterGraph._is_validation_command_executable(command, project_dir=project_dir)
                        if executable:
                            return command
            if any(tok in lowered for tok in ["test", "spec", "assertion", "verification"]):
                for key in ["test", "check", "build"]:
                    for command in repository_candidates.get(key, []):
                        executable, _ = DevMasterGraph._is_validation_command_executable(command, project_dir=project_dir)
                        if executable:
                            return command
        return ""

    @staticmethod
    def _dev_preflight_planning(state: DevGraphState) -> DevGraphState:
        return dev_preflight_planning_phase(state, DevMasterGraph)

    @staticmethod
    def _ask_cli_clarifications_if_needed(state: DevGraphState) -> DevGraphState:
        return ask_cli_clarifications_phase(state, DevMasterGraph)

    @staticmethod
    def _prepare_execution_steps(state: DevGraphState) -> DevGraphState:
        return prepare_execution_steps_phase(state, DevMasterGraph)

    @staticmethod
    def _compute_discovery_candidates(state: DevGraphState) -> List[Dict[str, Any]]:
        repo_root = os.path.dirname(str(state.get("scope_root", "")).rstrip(os.sep))
        requirement_text = " ".join(
            [
                str(state.get("plan", {}).get("summary", "")),
                " ".join([str(x) for x in state.get("plan", {}).get("constraints", []) if isinstance(x, str)]),
                " ".join([str(x) for x in state.get("plan", {}).get("validation", []) if isinstance(x, str)]),
            ]
        ).strip()
        ctx = scan_workspace_context(repo_root, file_limit=600)
        ranked = rank_candidate_files(requirement_text, ctx.get("sampled_files", []), top_k=80)
        state["dev_discovery_candidates"] = ranked
        DevMasterGraph._emit_event(
            state,
            "dev_discovery_ranked",
            requirement_excerpt=DevMasterGraph._sanitize_text(requirement_text, 300),
            candidate_count=len(ranked),
            top_candidates=ranked[:15],
        )
        return ranked

    @staticmethod
    def _build_dev_technical_plan(state: DevGraphState) -> Dict[str, Any]:
        targets = state.get("implementation_targets", [])
        steps = state.get("bootstrap_tasks", [])
        validations = state.get("validation_tasks", [])
        affected_files: List[Dict[str, Any]] = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            affected_files.append(
                {
                    "path_hint": str(target.get("expected_path_hint", "")),
                    "file_name": str(target.get("file_name", "")),
                    "change_type": str(target.get("modification_type", "modify")),
                    "creation_policy": str(target.get("creation_policy", "")),
                    "rationale": str(target.get("details", "")),
                }
            )
        todos: List[Dict[str, Any]] = []
        for idx, target in enumerate(affected_files, start=1):
            todos.append(
                {
                    "id": f"dev_todo_{idx}",
                    "description": f"{target['change_type']} {target['path_hint'] or target['file_name']}".strip(),
                    "acceptance_criteria": [
                        "File change is applied and syntactically valid",
                        "Change aligns with PM acceptance criteria and constraints",
                    ],
                }
            )
        command_plan = [
            {"cwd": str(task.cwd or "."), "command": str(task.command or ""), "purpose": str(task.description)}
            for task in steps
            if isinstance(task, DevTask)
        ]
        validation_plan = [
            {"id": str(task.id), "description": str(task.description), "command": str(task.command or "")}
            for task in validations
            if isinstance(task, DevTask)
        ]
        technical_plan = {
            "project_root": str(state.get("project_root", "")),
            "affected_files": affected_files,
            "command_plan": command_plan,
            "todo_plan": todos,
            "validation_plan": validation_plan,
            "discovery_candidates": state.get("dev_discovery_candidates", [])[:20],
        }
        state["dev_technical_plan"] = technical_plan
        DevMasterGraph._emit_event(
            state,
            "dev_technical_plan_built",
            affected_files=affected_files,
            command_count=len(command_plan),
            todo_count=len(todos),
            validation_count=len(validation_plan),
        )
        return technical_plan

    @staticmethod
    def _is_within_scope(scope_root: str, candidate_path: str) -> bool:
        try:
            scope_abs = os.path.abspath(scope_root)
            cand_abs = os.path.abspath(candidate_path)
            return os.path.commonpath([scope_abs, cand_abs]) == scope_abs
        except Exception:
            return False

    @staticmethod
    def _has_project_marker(path: str) -> bool:
        if not os.path.isdir(path):
            return False
        try:
            names = set(os.listdir(path))
        except Exception:
            return False
        if any(marker in names for marker in DevMasterGraph.PROJECT_MARKER_FILES):
            return True
        return any(name.endswith(".csproj") or name.endswith(".sln") for name in names)

    @staticmethod
    def _source_hint_count(path: str) -> int:
        if not os.path.isdir(path):
            return 0
        count = 0
        for name in DevMasterGraph.SOURCE_DIR_HINTS:
            if os.path.isdir(os.path.join(path, name)):
                count += 1
        return count

    @staticmethod
    def _normalize_target_tail(expected_path_hint: str, project_name: str) -> str:
        normalized = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        if normalized.startswith("projects/"):
            parts = [p for p in normalized.split("/") if p]
            if len(parts) >= 3:
                if project_name and parts[1] != project_name:
                    return "/".join(parts[2:])
                return "/".join(parts[2:])
            return ""
        return normalized

    @staticmethod
    def _resolve_active_project_root_after_bootstrap(
        *,
        state: DevGraphState,
        attempt_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        scope_root = str(state.get("scope_root", "")).strip()
        project_root = str(state.get("project_root", "")).strip()
        project_name = str(state.get("project_name", "")).strip()
        active_root = str(state.get("active_project_root", "")).strip()
        if not scope_root:
            return {"selected_root": active_root, "confidence": 0, "candidates": [], "ambiguous": False}

        scope_abs = os.path.abspath(scope_root)
        expected_tails = [
            DevMasterGraph._normalize_target_tail(str(t.get("expected_path_hint", "")), project_name)
            for t in state.get("implementation_targets", [])
            if isinstance(t, dict)
        ]
        expected_tails = [x for x in expected_tails if x]

        score_map: Dict[str, int] = {}
        reasons_map: Dict[str, List[str]] = {}

        def _add_candidate(path: str, score: int, reason: str) -> None:
            if not path:
                return
            cand_abs = canonicalize_scope_path(scope_abs, path)
            if not DevMasterGraph._is_within_scope(scope_abs, cand_abs):
                return
            if not os.path.isdir(cand_abs):
                return
            score_map[cand_abs] = score_map.get(cand_abs, 0) + int(score)
            reasons_map.setdefault(cand_abs, []).append(reason)

        # Seed candidates from known roots/cwds.
        if active_root:
            _add_candidate(active_root, 25, "initial_active_root")
        if project_root:
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            _add_candidate(os.path.join(scope_abs, rel), 15, "project_root_hint")

        # Parse stdout/stderr for path evidence.
        regexes = [
            r"Success!\s+Created\s+.+?\s+at\s+([^\r\n]+)",
            r"Scaffolding project in\s+([^\r\n]+)",
            r"Created project at\s+([^\r\n]+)",
            r"Project created at\s+([^\r\n]+)",
        ]
        for attempt in attempt_history:
            blob = f"{attempt.get('stdout', '')}\n{attempt.get('stderr', '')}"
            for pattern in regexes:
                for m in re.finditer(pattern, blob, flags=re.IGNORECASE):
                    candidate = m.group(1).strip().rstrip(".")
                    _add_candidate(candidate, 90, f"stdout_pattern:{pattern}")
            attempt_cwd = str(attempt.get("cwd", "")).strip()
            if attempt_cwd:
                _add_candidate(attempt_cwd, 12, "attempt_cwd")

        for touched in state.get("touched_paths", []):
            if isinstance(touched, str):
                _add_candidate(touched, 10, "touched_path")

        # Scan immediate and nested directories for marker evidence.
        search_roots: Set[str] = set()
        for base in list(score_map.keys()):
            search_roots.add(base)
            parent = os.path.dirname(base)
            if parent and DevMasterGraph._is_within_scope(scope_abs, parent):
                search_roots.add(parent)

        ignore_dirs = {"node_modules", ".git", ".venv", "__pycache__", "dist", "build", ".next", ".cache"}
        for search_root in list(search_roots):
            if not os.path.isdir(search_root):
                continue
            for root, dirs, _files in os.walk(search_root):
                dirs[:] = [d for d in dirs if d not in ignore_dirs]
                depth = os.path.relpath(root, search_root).count(os.sep)
                if depth > 3:
                    dirs[:] = []
                    continue
                if DevMasterGraph._has_project_marker(root):
                    _add_candidate(root, max(40, 70 - (depth * 10)), f"marker_depth:{depth}")
                hints = DevMasterGraph._source_hint_count(root)
                if hints:
                    _add_candidate(root, hints * 6, f"source_hints:{hints}")

        for candidate in list(score_map.keys()):
            base = os.path.basename(candidate.rstrip("/\\"))
            if project_name and base == project_name:
                _add_candidate(candidate, 8, "basename_matches_project")
            if expected_tails:
                matches = 0
                for tail in expected_tails:
                    if os.path.exists(os.path.join(candidate, tail)):
                        matches += 1
                if matches:
                    _add_candidate(candidate, matches * 20, f"target_tail_matches:{matches}")

        ranked = sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)
        candidates = [
            {"path": path, "score": score, "reasons": reasons_map.get(path, [])}
            for path, score in ranked
        ]
        if not candidates:
            return {
                "selected_root": active_root,
                "confidence": 0,
                "candidates": [],
                "ambiguous": False,
            }

        top = candidates[0]
        second_score = candidates[1]["score"] if len(candidates) > 1 else -999
        confidence = int(top["score"])
        ambiguous = len(candidates) > 1 and confidence < 60 and (top["score"] - second_score) < 10
        return {
            "selected_root": top["path"],
            "confidence": confidence,
            "candidates": candidates[:5],
            "ambiguous": ambiguous,
        }

    @staticmethod
    def _build_llm_context_contract(state: DevGraphState) -> Dict[str, Any]:
        scope_root = str(state.get("scope_root", "")).strip()
        resolved_root = str(state.get("active_project_root", "")).strip()
        project_name = str(state.get("project_name", "")).strip()
        root_evidence = state.get("root_resolution_evidence", {}) if isinstance(state.get("root_resolution_evidence"), dict) else {}
        normalized_targets: List[Dict[str, str]] = []
        for target in state.get("implementation_targets", []):
            if not isinstance(target, dict):
                continue
            expected = str(target.get("expected_path_hint", ""))
            file_name = str(target.get("file_name", ""))
            creation_policy = str(target.get("creation_policy", ""))
            resolved = ""
            try:
                resolved = DevMasterGraph._resolve_target_file_path(
                    scope_root=scope_root,
                    project_root=str(state.get("project_root", "")),
                    active_project_root=resolved_root,
                    expected_path_hint=expected,
                    file_name=file_name,
                )
            except Exception:
                resolved = ""
            normalized_targets.append(
                {
                    "expected_path_hint": expected,
                    "file_name": file_name,
                    "creation_policy": creation_policy,
                    "resolved_absolute_path": resolved,
                }
            )

        tree_snapshot: List[str] = []
        if resolved_root and os.path.isdir(resolved_root):
            try:
                entries = sorted(os.listdir(resolved_root))[:30]
                tree_snapshot = entries
            except Exception:
                tree_snapshot = []

        return {
            "scope_root": scope_root,
            "resolved_active_root": resolved_root,
            "project_name": project_name,
            "candidate_roots": root_evidence.get("candidates", []),
            "root_confidence": root_evidence.get("confidence", 0),
            "path_aliases": {
                "project_root": str(state.get("project_root", "")),
                "active_project_root": resolved_root,
            },
            "normalized_targets": normalized_targets,
            "active_root_tree_snapshot": tree_snapshot,
        }

    @staticmethod
    def _build_active_root_file_index(active_root: str) -> Dict[str, Any]:
        files: List[str] = []
        by_basename: Dict[str, List[str]] = {}
        by_basename_casefold: Dict[str, List[str]] = {}
        by_suffix_casefold: Dict[str, str] = {}
        if not active_root or not os.path.isdir(active_root):
            return {
                "active_root": active_root,
                "files": files,
                "by_basename": by_basename,
                "by_basename_casefold": by_basename_casefold,
                "by_suffix_casefold": by_suffix_casefold,
                "cognition": {
                    "version": "2.0",
                    "active_root": active_root,
                    "file_count": 0,
                    "symbol_index": {"files": [], "by_name": {}},
                    "entrypoints": [],
                    "entrypoint_aliases": {},
                    "resolution_hints": {},
                    "provider_capabilities": {},
                },
            }
        for root, dirs, names in os.walk(active_root):
            dirs[:] = [d for d in dirs if d not in DevMasterGraph.INDEX_IGNORE_DIRS]
            for name in names:
                abs_path = os.path.join(root, name)
                rel = os.path.relpath(abs_path, active_root).replace("\\", "/")
                files.append(rel)
                base = os.path.basename(rel)
                by_basename.setdefault(base, []).append(rel)
                by_basename_casefold.setdefault(base.casefold(), []).append(rel)
                suffixes = rel.split("/")
                for idx in range(len(suffixes)):
                    suffix = "/".join(suffixes[idx:]).casefold()
                    if suffix and suffix not in by_suffix_casefold:
                        by_suffix_casefold[suffix] = rel
        cognition = build_cognition_index(active_root, files)
        scaffold_probe = probe_scaffold_layout(active_root, limit=1200)
        return {
            "active_root": active_root,
            "files": files,
            "by_basename": by_basename,
            "by_basename_casefold": by_basename_casefold,
            "by_suffix_casefold": by_suffix_casefold,
            "scaffold_probe": scaffold_probe,
            "cognition": cognition,
        }

    @staticmethod
    def _emit_index_snapshot(state: DevGraphState, index: Dict[str, Any], category: str) -> None:
        files = index.get("files", []) if isinstance(index.get("files"), list) else []
        preview = sorted(files)[:30]
        DevMasterGraph._emit_event(
            state,
            category,
            active_root=DevMasterGraph._relpath_safe(state, str(index.get("active_root", ""))),
            file_count=len(files),
            top_entries=preview,
        )

    @staticmethod
    def _refresh_active_root_index(state: DevGraphState, *, category: str) -> Dict[str, Any]:
        active_root = str(state.get("active_project_root", "")).strip()
        scope_root = str(state.get("scope_root", "")).strip()
        if active_root and scope_root:
            active_root = canonicalize_scope_path(scope_root, active_root)
        if not active_root:
            project_root = str(state.get("project_root", "")).strip()
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            active_root = os.path.join(scope_root, rel) if scope_root else active_root
        if active_root and scope_root:
            active_root = canonicalize_scope_path(scope_root, active_root)
            state["active_project_root"] = active_root
        index = DevMasterGraph._build_active_root_file_index(active_root)
        state["active_root_file_index"] = index
        DevMasterGraph._emit_index_snapshot(state, index, category)
        probe = index.get("scaffold_probe", {}) if isinstance(index.get("scaffold_probe"), dict) else {}
        if probe:
            files = probe.get("files", []) if isinstance(probe.get("files"), list) else []
            top_level = probe.get("top_level", []) if isinstance(probe.get("top_level"), list) else []
            DevMasterGraph._emit_event(
                state,
                "scaffold_probe_snapshot",
                phase=category,
                file_count=len(files),
                top_level=top_level[:30],
            )
        cognition = index.get("cognition", {}) if isinstance(index.get("cognition"), dict) else {}
        providers = cognition.get("provider_capabilities", {}) if isinstance(cognition, dict) else {}
        if providers:
            DevMasterGraph._emit_event(state, "cognition_provider_capabilities", **providers)
        request_id = str(state.get("request_id", "")).strip()
        project_name = str(state.get("project_name", "")).strip() or derive_project_name(state.get("plan", {}))
        if request_id and project_name and str(state.get("scope_root", "")).strip():
            snapshot_path = persist_cognition_snapshot(
                repo_root=str(state.get("scope_root", "")).strip(),
                project_name=project_name,
                run_id=request_id,
                phase=category,
                cognition_index=cognition if isinstance(cognition, dict) else {},
            )
            if snapshot_path:
                DevMasterGraph._emit_event(
                    state,
                    "cognition_snapshot_created",
                    phase=category,
                    snapshot_path=DevMasterGraph._relpath_safe(state, snapshot_path),
                )
        return index

    @staticmethod
    def _normalize_expected_suffix_for_active_root(expected_path_hint: str, active_root: str, project_root: str) -> str:
        expected = str(expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        if not expected:
            return ""
        parts = [p for p in expected.split("/") if p]
        if expected.startswith("projects/") and len(parts) >= 3:
            parts = parts[2:]
        active_parts = [p for p in str(active_root or "").replace("\\", "/").split("/") if p]
        project_parts = [p for p in str(project_root or "").replace("\\", "/").split("/") if p]
        if len(active_parts) >= 1 and len(project_parts) >= 1:
            if active_parts[-1].casefold() == project_parts[-1].casefold():
                pass
        # Deduplicate active root tail from expected suffix, e.g. active_root=.../frontend and suffix starts frontend/
        if active_parts:
            tail = active_parts[-1].casefold()
            if parts and parts[0].casefold() == tail:
                parts = parts[1:]
        return "/".join(parts)

    @staticmethod
    def _choose_best_index_candidate(
        *,
        index: Dict[str, Any],
        expected_suffix: str,
        file_name: str,
    ) -> str:
        suffix_map = index.get("by_suffix_casefold", {}) if isinstance(index.get("by_suffix_casefold"), dict) else {}
        by_basename = index.get("by_basename_casefold", {}) if isinstance(index.get("by_basename_casefold"), dict) else {}
        cognition = index.get("cognition", {}) if isinstance(index.get("cognition"), dict) else {}
        resolution_hints = cognition.get("resolution_hints", {}) if isinstance(cognition.get("resolution_hints"), dict) else {}
        entrypoint_aliases = cognition.get("entrypoint_aliases", {}) if isinstance(cognition.get("entrypoint_aliases"), dict) else {}
        entrypoint_candidates = cognition.get("entrypoints", []) if isinstance(cognition.get("entrypoints"), list) else []
        ai_ranked_candidates = resolution_hints.get("ai_ranked_candidates", []) if isinstance(resolution_hints, dict) else []
        expected_low = expected_suffix.casefold().strip("/")
        if expected_low and expected_low in suffix_map:
            return str(suffix_map[expected_low])
        leaf = os.path.basename(str(file_name or "").replace("\\", "/")).strip()
        if leaf:
            candidates = by_basename.get(leaf.casefold(), [])
            if isinstance(candidates, list) and candidates:
                if expected_low:
                    scored: List[Tuple[int, str]] = []
                    expected_parts = [p for p in expected_low.split("/") if p]
                    for rel in candidates:
                        rel_low = str(rel).casefold()
                        score = 0
                        for part in expected_parts[:-1]:
                            if f"/{part}/" in f"/{rel_low}/":
                                score += 1
                        scored.append((score, rel))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    return str(scored[0][1])
                return str(candidates[0])
            hinted = resolution_hints.get("by_basename", {}) if isinstance(resolution_hints, dict) else {}
            hinted_candidates = hinted.get(leaf.casefold(), []) if isinstance(hinted, dict) else []
            if isinstance(hinted_candidates, list) and hinted_candidates:
                return str(hinted_candidates[0])

        # Entrypoint alias recovery for common and unknown scaffold conventions.
        expected_parent = os.path.dirname(expected_low)
        expected_base = os.path.basename(expected_low)
        if expected_base.startswith("index.") or expected_base.startswith("main.") or expected_base.startswith("app."):
            sibling_candidates: List[str] = []
            if expected_parent in entrypoint_aliases and isinstance(entrypoint_aliases[expected_parent], list):
                sibling_candidates.extend([str(x) for x in entrypoint_aliases[expected_parent]])
            if not sibling_candidates:
                for item in entrypoint_candidates:
                    candidate_path = str(item.get("path", ""))
                    if not candidate_path:
                        continue
                    if expected_parent and os.path.dirname(candidate_path.casefold()) != expected_parent:
                        continue
                    sibling_candidates.append(candidate_path)
            if sibling_candidates:
                preferred = sorted(
                    sibling_candidates,
                    key=lambda x: float(
                        next(
                            (
                                item.get("score", 0.0)
                                for item in entrypoint_candidates
                                if str(item.get("path", "")) == x
                            ),
                            0.0,
                        )
                    ),
                    reverse=True,
                )
                return str(preferred[0])
        if isinstance(ai_ranked_candidates, list) and ai_ranked_candidates:
            return str(ai_ranked_candidates[0].get("path", ""))
        return ""

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

    @staticmethod
    def _resolve_target_file_path(
        *,
        scope_root: str,
        project_root: str,
        active_project_root: str,
        expected_path_hint: str,
        file_name: str,
    ) -> str:
        scope_abs = os.path.abspath(os.path.normpath(scope_root))
        expected_norm = (expected_path_hint or "").replace("\\", "/").strip()
        while expected_norm.startswith("./"):
            expected_norm = expected_norm[2:]
        project_root_norm = (project_root or "").replace("\\", "/").strip()
        while project_root_norm.startswith("./"):
            project_root_norm = project_root_norm[2:]
        file_name_norm = (file_name or "").strip()

        project_name = ""
        if project_root_norm.startswith("projects/"):
            parts = [p for p in project_root_norm.split("/") if p]
            if len(parts) >= 2:
                project_name = parts[1]

        if active_project_root:
            active_abs = canonicalize_scope_path(scope_abs, active_project_root)
            if os.path.commonpath([scope_abs, active_abs]) == scope_abs:
                base_root = active_abs
            else:
                raise RuntimeError(f"Active project root escapes scope: {active_project_root}")
        else:
            rel = project_root_norm.split("/", 1)[1] if project_root_norm.startswith("projects/") else project_root_norm
            base_root = canonicalize_scope_path(scope_abs, os.path.join(scope_abs, rel))

        file_name_norm = file_name_norm.replace("\\", "/").strip()
        while file_name_norm.startswith("./"):
            file_name_norm = file_name_norm[2:]
        file_leaf = os.path.basename(file_name_norm) if file_name_norm else ""
        rel_path = expected_norm
        if expected_norm.startswith("projects/"):
            parts = [p for p in expected_norm.split("/") if p]
            if len(parts) >= 3:
                # If PM project name drifted, still anchor to active root.
                if project_name and parts[1] != project_name:
                    rel_path = "/".join(parts[2:])
                else:
                    rel_path = "/".join(parts[2:])
            elif len(parts) == 2:
                rel_path = file_name_norm or parts[-1]
            else:
                rel_path = file_name_norm
        elif not rel_path:
            rel_path = file_name_norm

        if not rel_path and file_leaf:
            rel_path = file_leaf

        expected_has_extension = bool(os.path.splitext(rel_path)[1]) if rel_path else False
        if rel_path and not expected_has_extension and file_leaf:
            rel_parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
            if not rel_parts or rel_parts[-1] != file_leaf:
                rel_path = "/".join(rel_parts + [file_leaf]) if rel_parts else file_leaf

        # If active root already points to a nested app root (frontend/backend/src),
        # remove duplicated leading segments from expected relative path.
        active_tail = os.path.basename(base_root.rstrip("/\\")).casefold()
        rel_parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
        if active_tail and rel_parts and rel_parts[0].casefold() == active_tail:
            rel_path = "/".join(rel_parts[1:])

        safe_path = canonicalize_scope_path(scope_abs, os.path.join(base_root, rel_path))
        if os.path.commonpath([scope_abs, safe_path]) != scope_abs:
            raise RuntimeError(f"Implementation target escapes scope: {expected_path_hint}")
        return safe_path

    @staticmethod
    def _discover_existing_path(
        active_root: str,
        expected_path_hint: str,
        file_name: str,
        *,
        project_root: str = "",
        file_index: Optional[Dict[str, Any]] = None,
        state: Optional[DevGraphState] = None,
    ) -> str:
        if not active_root or not os.path.isdir(active_root):
            return ""
        index = file_index or DevMasterGraph._build_active_root_file_index(active_root)
        expected_suffix = DevMasterGraph._normalize_expected_suffix_for_active_root(
            expected_path_hint,
            active_root,
            project_root,
        )
        probe_files = []
        scaffold_probe = index.get("scaffold_probe", {}) if isinstance(index.get("scaffold_probe"), dict) else {}
        if isinstance(scaffold_probe.get("files"), list):
            probe_files = [str(x) for x in scaffold_probe.get("files", [])]
        # Prefer scaffold-discovered concrete files for ambiguous paths.
        leaf = os.path.basename(str(file_name or "").replace("\\", "/")).strip().casefold()
        if leaf:
            by_leaf = [x for x in probe_files if os.path.basename(x).casefold() == leaf]
            if by_leaf:
                chosen = by_leaf[0]
                if state is not None:
                    key = expected_path_hint or file_name
                    evidence = state.setdefault("target_resolution_evidence", {})
                    evidence[key] = {
                        "resolved_path": chosen,
                        "resolution_method": "scaffold_probe",
                        "confidence": 0.9,
                        "candidates_considered": by_leaf[:8],
                    }
                return os.path.join(active_root, chosen)
        rel = DevMasterGraph._choose_best_index_candidate(
            index=index,
            expected_suffix=expected_suffix,
            file_name=file_name,
        )
        diagnostics_refs: List[str] = []
        if state is not None:
            memory = DevMasterGraph._ensure_repository_memory(state)
            refs = memory.get("diagnostic_file_refs", [])
            if isinstance(refs, list):
                diagnostics_refs = [str(x).replace("\\", "/").strip().casefold() for x in refs if str(x).strip()]
        if rel and state is not None and DevMasterGraph._has_recent_candidate_rejection(state, rel):
            rel = ""
        if not rel:
            by_basename = index.get("by_basename_casefold", {}) if isinstance(index.get("by_basename_casefold"), dict) else {}
            leaf = os.path.basename(str(file_name or "").replace("\\", "/")).strip().casefold()
            alternatives = list(by_basename.get(leaf, [])) if leaf else []
            if alternatives:
                boosted = sorted(
                    alternatives,
                    key=lambda candidate: (
                        1 if any(os.path.basename(candidate).casefold() in ref for ref in diagnostics_refs) else 0,
                        1 if expected_suffix and str(candidate).casefold().endswith(expected_suffix.casefold()) else 0,
                    ),
                    reverse=True,
                )
                for candidate in boosted:
                    if state is not None and DevMasterGraph._has_recent_candidate_rejection(state, candidate):
                        continue
                    rel = str(candidate)
                    break
        if not rel:
            if state is not None:
                key = expected_path_hint or file_name
                evidence = state.setdefault("target_resolution_evidence", {})
                evidence[key] = {
                    "resolved_path": "",
                    "resolution_method": "none",
                    "confidence": 0.0,
                    "candidates_considered": [],
                }
            return ""
        if state is not None:
            key = expected_path_hint or file_name
            confidence = 0.85 if os.path.basename(expected_suffix).casefold() == os.path.basename(rel).casefold() else 0.72
            evidence = state.setdefault("target_resolution_evidence", {})
            evidence[key] = {
                "resolved_path": rel,
                "resolution_method": "index_candidates",
                "confidence": confidence,
                "candidates_considered": [rel],
            }
            DevMasterGraph._remember(
                state,
                "candidate_attempts",
                {
                    "expected_path_hint": expected_path_hint,
                    "file_name": file_name,
                    "candidate_path": rel,
                    "confidence": confidence,
                    "resolution_method": evidence[key].get("resolution_method", ""),
                },
            )
        return os.path.join(active_root, rel)

    @staticmethod
    def _extract_error_file_refs(attempt_history: List[Dict[str, Any]]) -> List[str]:
        refs: List[str] = []
        patterns = [
            re.compile(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+):\d+:\d+"),
            re.compile(r"File \"([^\"]+)\""),
        ]
        for attempt in attempt_history:
            blob = f"{attempt.get('stdout', '')}\n{attempt.get('stderr', '')}"
            for pattern in patterns:
                for match in pattern.finditer(blob):
                    candidate = match.group(1).replace("\\", "/").strip()
                    if candidate and candidate not in refs:
                        refs.append(candidate)
        return refs[:40]

    @staticmethod
    def _classify_diagnostic_taxonomy(attempt_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        taxonomies: List[Dict[str, Any]] = []
        for attempt in attempt_history:
            blob = f"{attempt.get('stdout', '')}\n{attempt.get('stderr', '')}".lower()
            kind = "runtime"
            if any(tok in blob for tok in ["cannot find module", "module not found", "importerror", "no module named"]):
                kind = "module"
            elif any(tok in blob for tok in ["not found", "command not found", "is not recognized as an internal or external command"]):
                kind = "capability"
            elif any(tok in blob for tok in ["syntaxerror", "unexpected token", "parse error", "ts1005"]):
                kind = "syntax"
            elif any(tok in blob for tok in ["no such file", "cannot find the path", "enoent", "path"]):
                kind = "path"
            elif any(tok in blob for tok in ["test failed", "assert", "failing tests", "pytest"]):
                kind = "test"
            elif any(tok in blob for tok in ["config", "tsconfig", "package.json", "pyproject"]):
                kind = "config"
            taxonomies.append(
                {
                    "task_id": attempt.get("task_id", ""),
                    "taxonomy": kind,
                    "exit_code": attempt.get("exit_code"),
                    "category": attempt.get("category", "unknown"),
                }
            )
        return taxonomies

    @staticmethod
    def _infer_compile_recovery_commands(
        *,
        stacks: List[str],
        taxonomy: List[Dict[str, Any]],
        project_dir: str,
    ) -> List[str]:
        inferred: List[str] = []
        primary = taxonomy[0]["taxonomy"] if taxonomy else "runtime"
        _ = stacks
        if primary != "capability":
            repository_candidates = DevMasterGraph._discover_repository_command_candidates(project_dir)
            if primary in {"module", "config", "path"}:
                inferred.extend(repository_candidates.get("setup", []))
                inferred.extend(repository_candidates.get("build", []))
                inferred.extend(repository_candidates.get("check", []))
            elif primary == "test":
                inferred.extend(repository_candidates.get("test", []))
                inferred.extend(repository_candidates.get("check", []))
            else:
                inferred.extend(repository_candidates.get("build", []))
                inferred.extend(repository_candidates.get("check", []))
                inferred.extend(repository_candidates.get("test", []))
        if not inferred and primary != "capability":
            inferred = DevMasterGraph._infer_final_compile_commands(
                project_dir=project_dir,
                stacks=stacks,
                validation_commands=[],
            )
        deduped: List[str] = []
        for command in inferred:
            if command not in deduped:
                deduped.append(command)
        return deduped[:2]

    @staticmethod
    def _comment_for_path(path: str, text: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext in {".ts", ".tsx", ".js", ".jsx", ".java", ".c", ".cpp", ".go", ".rs"}:
            return f"// {text}\n"
        if ext in {".py", ".sh", ".rb", ".yml", ".yaml"}:
            return f"# {text}\n"
        return f"{text}\n"

    @staticmethod
    def _component_name_from_file(file_name: str) -> str:
        stem = os.path.splitext(os.path.basename(file_name))[0]
        cleaned = re.sub(r"[^A-Za-z0-9_]", "", stem)
        if not cleaned:
            return "GeneratedComponent"
        return cleaned[0].upper() + cleaned[1:]

    @staticmethod
    def _default_generated_content(path: str, file_name: str, details: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext in {".tsx", ".jsx"}:
            component_name = DevMasterGraph._component_name_from_file(file_name)
            return (
                f"export default function {component_name}() {{\n"
                f"  return <div>{details or 'Generated component'}</div>;\n"
                "}\n"
            )
        if ext in {".ts", ".js"}:
            return "export {};\n"
        if ext in {".py"}:
            return "def main() -> None:\n    pass\n\n\nif __name__ == '__main__':\n    main()\n"
        if ext == ".md":
            return f"# {file_name}\n\n{details or 'Generated content'}\n"
        return (details or "Generated content") + "\n"

    @staticmethod
    def _infer_rename_destination(*, safe_target: str, file_name: str, expected_path_hint: str, details: str) -> str:
        # Prefer explicit extension/path mention in details when available.
        detail_text = str(details or "").strip()
        match = re.search(r"\bto\s+([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)\b", detail_text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).replace("\\", "/").strip()
            leaf = os.path.basename(candidate)
            if leaf:
                return os.path.join(os.path.dirname(safe_target), leaf)

        # If expected path hint points to a file leaf, use it.
        expected = str(expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        expected_leaf = os.path.basename(expected)
        if expected_leaf and "." in expected_leaf and expected_leaf != os.path.basename(file_name):
            return os.path.join(os.path.dirname(safe_target), expected_leaf)

        # Fallback: if source is .ts and detail suggests tsx/jsx, map extension.
        src_leaf = os.path.basename(safe_target)
        if src_leaf.endswith(".ts") and ("tsx" in detail_text.lower() or "jsx" in detail_text.lower()):
            return os.path.join(os.path.dirname(safe_target), src_leaf[:-3] + ".tsx")
        return ""

    @staticmethod
    def _llm_generate_file_content(
        *,
        state: DevGraphState,
        target_path: str,
        file_name: str,
        details: str,
        existing_content: str,
    ) -> str:
        requirement = str(state.get("plan", {}).get("summary", ""))
        constraints = [str(x) for x in state.get("plan", {}).get("constraints", []) if isinstance(x, str)]
        validation = [str(x) for x in state.get("plan", {}).get("validation", []) if isinstance(x, str)]
        prompt = (
            "You are a senior software engineer. Generate file content for a single target file. "
            "Return ONLY the full file contents. No markdown fences.\n"
            f"Target path: {target_path}\n"
            f"File name: {file_name}\n"
            f"Task details: {details}\n"
            f"Requirement summary: {requirement}\n"
            f"Constraints: {constraints}\n"
            f"Validation expectations: {validation}\n"
            f"Existing content (may be empty):\n{existing_content[:6000]}"
        )
        try:
            from config import client  # lazy import to avoid hard dependency during tests
        except Exception:
            return DevMasterGraph._default_generated_content(target_path, file_name, details)
        if not hasattr(client, "responses"):
            return DevMasterGraph._default_generated_content(target_path, file_name, details)
        try:
            response = client.responses.create(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.1-codex-mini"),
                input=[{"role": "user", "content": prompt}],
            )
            for item in response.output:
                if item.type != "message":
                    continue
                for part in item.content:
                    if part.type == "output_text":
                        text = str(part.text or "").strip()
                        if text:
                            return text + ("" if text.endswith("\n") else "\n")
        except Exception:
            pass
        return DevMasterGraph._default_generated_content(target_path, file_name, details)

    @staticmethod
    def _apply_target_in_pass(
        *,
        state: DevGraphState,
        safe_target: str,
        file_name: str,
        active_root: str,
        modification_type: str,
        details: str,
        pass_index: int,
        expected_path_hint: str,
        creation_policy: str,
        file_index: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, str]:
        if modification_type in {"create_directory", "mkdir", "create_dir"}:
            os.makedirs(safe_target, exist_ok=True)
            return "ensured_directory", f"pass={pass_index}", safe_target

        rename_like = modification_type in {"rename", "move", "rename_file", "mv"}
        if rename_like:
            if not os.path.exists(safe_target):
                discovered = DevMasterGraph._discover_existing_path(
                    active_root,
                    expected_path_hint,
                    file_name,
                    project_root=str(state.get("project_root", "")),
                    file_index=file_index,
                    state=state,
                )
                if discovered:
                    safe_target = discovered
            if not os.path.exists(safe_target):
                return "missing_expected_file", "rename_source_missing", safe_target
            destination = DevMasterGraph._infer_rename_destination(
                safe_target=safe_target,
                file_name=file_name,
                expected_path_hint=expected_path_hint,
                details=details,
            )
            if not destination:
                return "invalid_operation", "rename_destination_unresolved", safe_target
            changed, note = rename_path(src_path=safe_target, dest_path=destination)
            if not changed and note == "noop_same_path":
                return "observed_file", "rename_noop_same_path", safe_target
            if not changed:
                return "invalid_operation", f"rename_failed:{note}", safe_target
            state.setdefault("target_resolution_evidence", {})[expected_path_hint or file_name] = {
                "resolved_path": DevMasterGraph._relpath_safe(state, destination),
                "resolution_method": "rename_operation",
                "confidence": 0.95,
                "candidates_considered": [DevMasterGraph._relpath_safe(state, safe_target), DevMasterGraph._relpath_safe(state, destination)],
            }
            return "renamed_file", note, destination

        def _looks_like_file_target(path: str, file_hint: str) -> bool:
            hint_leaf = os.path.basename(str(file_hint or "").replace("\\", "/")).strip()
            path_leaf = os.path.basename(str(path or "").replace("\\", "/")).strip()
            if hint_leaf.startswith(".") or path_leaf.startswith("."):
                return True
            if os.path.splitext(hint_leaf)[1]:
                return True
            if os.path.splitext(path_leaf)[1]:
                return True
            return False

        is_directory_target = modification_type in {"create_directory", "mkdir", "create_dir"}
        if not is_directory_target and _looks_like_file_target(safe_target, file_name):
            is_directory_target = False
        elif not is_directory_target:
            is_directory_target = False

        if is_directory_target:
            os.makedirs(safe_target, exist_ok=True)
            return "ensured_path", f"pass={pass_index}", safe_target

        os.makedirs(os.path.dirname(safe_target), exist_ok=True)
        if os.path.isdir(safe_target):
            return "path_type_mismatch", "target_is_directory", safe_target
        update_like = modification_type in {"update", "replace", "modify", "patch"}
        verify_like = modification_type in {"verify", "inspect", "check"}
        must_exist = str(creation_policy or "").strip().lower() == "must_exist" or update_like or verify_like
        if update_like and not os.path.exists(safe_target):
            discovered = DevMasterGraph._discover_existing_path(
                active_root,
                expected_path_hint,
                file_name,
                project_root=str(state.get("project_root", "")),
                file_index=file_index,
                state=state,
            )
            if discovered:
                safe_target = discovered
            else:
                return "missing_expected_file", "requires_discovery_or_clarification", safe_target
        if verify_like and not os.path.exists(safe_target):
            discovered = DevMasterGraph._discover_existing_path(
                active_root,
                expected_path_hint,
                file_name,
                project_root=str(state.get("project_root", "")),
                file_index=file_index,
                state=state,
            )
            if discovered:
                return "observed_file", "verify_discovered_target", discovered
            return "missing_expected_file", "verify_missing_target", safe_target
        if not os.path.exists(safe_target):
            if must_exist:
                return "missing_expected_file", "not_created_due_to_update_policy", safe_target
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(
                    DevMasterGraph._llm_generate_file_content(
                        state=state,
                        target_path=safe_target,
                        file_name=file_name,
                        details=details,
                        existing_content="",
                    )
                )
            return "created_file", "generated_content", safe_target

        with open(safe_target, "r", encoding="utf-8", errors="ignore") as fh:
            original = fh.read()
        new_content = DevMasterGraph._llm_generate_file_content(
            state=state,
            target_path=safe_target,
            file_name=file_name,
            details=details,
            existing_content=original,
        )

        patched_content, changed = patch_region(original, new_content)
        if changed:
            low_signal = new_content.strip().casefold() in {
                original.strip().casefold(),
                f"// {details}".strip().casefold(),
                f"# {details}".strip().casefold(),
            }
            if low_signal:
                return "low_signal_update_rejected", "comment_or_noop_like_update", safe_target
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(patched_content)
            return "updated_file", "generated_update", safe_target

        if os.path.getsize(safe_target) == 0:
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(
                    DevMasterGraph._llm_generate_file_content(
                        state=state,
                        target_path=safe_target,
                        file_name=file_name,
                        details=details,
                        existing_content="",
                    )
                )
            return "updated_file", "filled_empty_file", safe_target
        return "observed_file", "already_up_to_date", safe_target

    @staticmethod
    def _run_implementation_review(state: DevGraphState, active_root: str, targets: List[Dict[str, str]]) -> List[str]:
        _ = (active_root, targets)
        findings: List[str] = []
        touched = [str(p) for p in state.get("touched_paths", []) if isinstance(p, str)]
        for path in touched:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except Exception:
                continue
            lowered = content.lower()
            if "implement pass" in lowered or "\n// implement:" in lowered or "\n# implement:" in lowered:
                findings.append(f"placeholder marker found in {DevMasterGraph._relpath_safe(state, path)}")
            if "todo" in lowered and len(content.strip()) < 220:
                findings.append(f"file appears TODO-only in {DevMasterGraph._relpath_safe(state, path)}")
        return findings

    @staticmethod
    def _execute_implementation_phase_impl(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_implementation_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_implementation_phase")
        DevMasterGraph._emit(state, "[PHASE] implementation")
        DevMasterGraph._ensure_repository_memory(state)

        if state.get("bootstrap_status") == "failed":
            state["implementation_status"] = "impl_skipped"
            DevMasterGraph._emit(state, "[IMPLEMENTATION] skipped due to bootstrap_failed")
            state["phase_status"]["execute_implementation_phase"] = "skipped"
            return state

        scope_root = state["scope_root"]
        project_root = str(state.get("project_root", f"projects/{state.get('project_name', 'project')}"))
        active_root = str(state.get("active_project_root", "")).strip()
        if not active_root:
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            active_root = os.path.join(scope_root, rel)
        state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
        DevMasterGraph._emit(state, f"[CONTEXT] active_root={active_root}")
        DevMasterGraph._refresh_active_root_index(state, category="implementation_index_refresh")
        targets = state.get("implementation_targets", [])
        pending_targets: List[Tuple[int, Dict[str, str]]] = []
        target_proofs: Dict[str, Dict[str, Any]] = {}
        for idx, target in enumerate(targets, start=1):
            item = DevMasterGraph._find_checklist_item(state, f"todo_impl_{idx}")
            if item and str(item.get("status", "")) == "completed":
                continue
            pending_targets.append((idx, target))
            checklist_id = f"todo_impl_{idx}"
            DevMasterGraph._set_checklist_status(
                state,
                checklist_id,
                "in_progress",
                evidence={"phase": "implementation", "pass": 0},
            )
        if not pending_targets:
            state["implementation_status"] = "completed"
            state["phase_status"]["execute_implementation_phase"] = "completed"
            DevMasterGraph._emit(state, "[IMPLEMENTATION] no pending implementation checklist items")
            return state
        total_actions = 0
        failed_target_ids: List[str] = []
        try:
            for pass_index in (1, 2):
                pass_label = f"implementation_pass_{pass_index}"
                DevMasterGraph._emit(state, f"[PHASE] {pass_label}")
                DevMasterGraph._emit(
                    state,
                    f"[WHY_THIS_STEP] pass={pass_index} iterative target execution with context-aware mutation policy"
                )
                pass_actions = 0
                for idx, target in pending_targets:
                    file_index = DevMasterGraph._refresh_active_root_index(
                        state,
                        category="implementation_index_refresh",
                    )
                    try:
                        action, _resolved_target = execute_implementation_target(
                            state=state,
                            graph_cls=DevMasterGraph,
                            idx=idx,
                            target=target,
                            pass_index=pass_index,
                            scope_root=scope_root,
                            project_root=project_root,
                            active_root=active_root,
                            file_index=file_index,
                            target_proofs=target_proofs,
                        )
                    except Exception as target_error:
                        item_id = f"todo_impl_{idx}"
                        if item_id not in failed_target_ids:
                            failed_target_ids.append(item_id)
                        state["errors"].append(f"[IMPLEMENTATION_TARGET_ERROR] {item_id}: {target_error}")
                        DevMasterGraph._set_checklist_status(
                            state,
                            item_id,
                            "failed",
                            evidence={
                                "phase": "implementation",
                                "pass_index": pass_index,
                                "error": str(target_error),
                            },
                        )
                        DevMasterGraph._emit_event(
                            state,
                            "implementation_target_failed",
                            target_id=item_id,
                            pass_index=pass_index,
                            error=str(target_error),
                        )
                        continue
                    DevMasterGraph._refresh_active_root_index(
                        state,
                        category="post_file_mutation_index_refresh",
                    )
                    DevMasterGraph._remember(
                        state,
                        "correction_attempts",
                        {
                            "pass_index": pass_index,
                            "target_index": idx,
                            "action": action,
                        },
                    )
                    pass_actions += 1
                    total_actions += 1
                state["implementation_pass_statuses"].append(f"{pass_label}:completed:{pass_actions}")
                DevMasterGraph._emit(
                    state,
                    f"[PASS_SUMMARY] {pass_label} actions={pass_actions} touched_total={len(state.get('touched_paths', []))}"
                )
            for idx, target in pending_targets:
                key = f"todo_impl_{idx}"
                if key in failed_target_ids:
                    continue
                proof = target_proofs.get(key, {})
                before_hash = str(proof.get("before_hash", ""))
                after_hash = str(proof.get("after_hash", ""))
                before_path = str(proof.get("before_path", ""))
                after_path = str(proof.get("after_path", ""))
                action = str(proof.get("action", ""))
                mod_type = str(target.get("modification_type", "")).strip().lower()
                if mod_type in {"verify", "inspect", "check"}:
                    DevMasterGraph._set_checklist_status(
                        state,
                        f"todo_impl_{idx}",
                        "completed",
                        evidence={"phase": "implementation", "mode": "verify", "target": key},
                    )
                    continue
                if before_hash == after_hash:
                    rename_like_proof = action == "renamed_file" and before_path and after_path and before_path != after_path
                    if rename_like_proof:
                        DevMasterGraph._set_checklist_status(
                            state,
                            f"todo_impl_{idx}",
                            "completed",
                            evidence={
                                "phase": "implementation",
                                "before_hash": before_hash,
                                "after_hash": after_hash,
                                "before_path": DevMasterGraph._relpath_safe(state, before_path),
                                "after_path": DevMasterGraph._relpath_safe(state, after_path),
                                "action": action,
                            },
                        )
                        continue
                    raise RuntimeError(
                        f"Target mutation proof missing for {key or f'implementation_target_{idx}'}: no content delta detected."
                    )
                DevMasterGraph._set_checklist_status(
                    state,
                    f"todo_impl_{idx}",
                    "completed",
                    evidence={
                        "phase": "implementation",
                        "before_hash": before_hash,
                        "after_hash": after_hash,
                    },
                )
            review_findings = DevMasterGraph._run_implementation_review(
                state=state,
                active_root=active_root,
                targets=targets,
            )
            DevMasterGraph._emit_event(
                state,
                "implementation_review",
                findings=review_findings,
                passed=not bool(review_findings),
            )
            if review_findings:
                raise RuntimeError("review gate failed: " + "; ".join(review_findings))
        except Exception as e:
            state["errors"].append(f"[IMPLEMENTATION_ERROR] {e}")
            DevMasterGraph._remember(
                state,
                "candidate_rejections",
                {
                    "reason": str(e),
                    "phase": "implementation",
                },
            )
            state["implementation_status"] = "blocked"
            state["phase_status"]["execute_implementation_phase"] = "failed"
            return state

        pending_or_failed = [
            item
            for item in state.get("internal_checklist", [])
            if isinstance(item, dict)
            and str(item.get("kind", "")) == "implementation"
            and str(item.get("status", "")) != "completed"
        ]
        state["implementation_status"] = "completed" if not pending_or_failed else "partial_progress"
        state["phase_status"]["execute_implementation_phase"] = "completed"
        DevMasterGraph._emit(state, f"[IMPLEMENTATION_SUMMARY] total_actions={total_actions}")
        return state

    @staticmethod
    def _execute_validation_phase_impl(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_validation_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_validation_phase")
        DevMasterGraph._ensure_repository_memory(state)
        if state.get("bootstrap_status") == "failed" or state.get("implementation_status") == "failed":
            state["validation_status"] = "skipped"
            state["phase_status"]["execute_validation_phase"] = "skipped"
            DevMasterGraph._emit(state, "[VALIDATION] skipped due to previous failure")
            return state

        preflight = state.get("dev_preflight_plan", {}) if isinstance(state.get("dev_preflight_plan"), dict) else {}
        raw_requirements = preflight.get("raw_validation_requirements", [])
        unresolved_requirements = preflight.get("unresolved_validation_requirements", [])
        unresolved_text = (
            [str(item.get("requirement", "")) for item in unresolved_requirements if isinstance(item, dict)]
            if isinstance(unresolved_requirements, list)
            else []
        )
        validation_tasks = state.get("validation_tasks", [])

        if raw_requirements and unresolved_requirements and not validation_tasks:
            msg = (
                "[VALIDATION] required validations were provided by PM but none were executable: "
                f"{unresolved_text or unresolved_requirements}"
            )
            for item in state.get("internal_checklist", []):
                if isinstance(item, dict) and str(item.get("kind")) == "validation":
                    item["status"] = "blocked"
                    DevMasterGraph._append_item_evidence(
                        item,
                        {"phase": "validation", "warning": msg, "non_executable_requirements": unresolved_requirements},
                    )
            state["validation_status"] = "skipped"
            state["phase_status"]["execute_validation_phase"] = "skipped"
            DevMasterGraph._remember(
                state,
                "validation_inference",
                {
                    "classification": "non_executable",
                    "raw_requirements": raw_requirements,
                    "unresolved_requirements": unresolved_requirements,
                    "fallback_policy": "compile_gate_must_still_run",
                },
            )
            DevMasterGraph._emit_event(
                state,
                "validation_skipped_non_executable",
                unresolved_requirements=unresolved_requirements,
                raw_requirements=raw_requirements,
            )
            DevMasterGraph._emit(state, msg)
            return state

        filtered_validation_tasks: List[DevTask] = []
        for task in validation_tasks:
            item = DevMasterGraph._find_checklist_item(state, f"todo_{task.id}")
            if item and str(item.get("status", "")) == "completed":
                continue
            filtered_validation_tasks.append(task)

        if not filtered_validation_tasks:
            state["validation_status"] = "completed"
            state["phase_status"]["execute_validation_phase"] = "completed"
            DevMasterGraph._emit(state, "[VALIDATION] no pending executable validations; marked completed")
            return state

        active_root = str(state.get("active_project_root", "")).strip()
        if active_root:
            filtered_validation_tasks = [
                DevTask(
                    id=task.id,
                    description=task.description,
                    command=task.command,
                    cwd=active_root,
                    kind=task.kind,
                )
                for task in filtered_validation_tasks
            ]
            DevMasterGraph._emit(state, f"[VALIDATION] reconciled task cwd to active root {active_root}")
        state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
        for task in filtered_validation_tasks:
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{task.id}",
                "in_progress",
                evidence={"phase": "validation", "task_id": task.id},
            )

        logs, touched_paths, errors, attempt_history, pending, outcomes = execute_dev_tasks(
            filtered_validation_tasks,
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
            command_run_mode="auto",
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
                        "phase": "validation",
                        "task_id": outcome.get("task_id"),
                        "category": outcome.get("category", "unknown"),
                        "exit_code": outcome.get("exit_code"),
                        "stdout_excerpt": outcome.get("stdout_excerpt", ""),
                        "stderr_excerpt": outcome.get("stderr_excerpt", ""),
                    },
                )
        error_file_refs = DevMasterGraph._extract_error_file_refs(attempt_history)
        if error_file_refs:
            DevMasterGraph._record_error_file_refs(state, error_file_refs)
            DevMasterGraph._emit_event(
                state,
                "validation_error_file_refs",
                refs=error_file_refs,
            )
        for outcome in outcomes:
            checklist_id = f"todo_{outcome.get('task_id', '')}"
            status = "completed" if outcome.get("status") == "completed" else "failed"
            DevMasterGraph._set_checklist_status(state, checklist_id, status, evidence=outcome)
        if pending:
            state["errors"].append(f"[VALIDATION] pending llm recovery unsupported for validation: {pending.get('task_id')}")
        if errors or pending:
            state["errors"].extend(errors)
            if error_file_refs:
                state["errors"].append(
                    "[RECOVERABLE_CONTEXT_GAP] validation failed with file-level diagnostics; "
                    f"targeted fix candidates={error_file_refs[:8]}"
                )
            state["validation_status"] = "failed"
            state["phase_status"]["execute_validation_phase"] = "failed"
            DevMasterGraph._emit(state, "[VALIDATION] failed")
            return state
        state["validation_status"] = "completed"
        state["phase_status"]["execute_validation_phase"] = "completed"
        DevMasterGraph._emit(state, "[VALIDATION] completed")
        return state

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

    @staticmethod
    def _execute_bootstrap_phase(state: DevGraphState) -> DevGraphState:
        return execute_bootstrap_phase(state, DevMasterGraph)

    @staticmethod
    def _execute_implementation_phase(state: DevGraphState) -> DevGraphState:
        return execute_implementation_phase(state, DevMasterGraph)

    @staticmethod
    def _execute_validation_phase(state: DevGraphState) -> DevGraphState:
        return execute_validation_phase(state, DevMasterGraph)

    @staticmethod
    def _execute_final_compile_gate(state: DevGraphState) -> DevGraphState:
        return execute_final_compile_gate(state, DevMasterGraph)

    @staticmethod
    def _finalize_result(state: DevGraphState) -> DevGraphState:
        return finalize_result_phase(state, DevMasterGraph)

    @staticmethod
    def _finalize_result_impl(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "finalize_result"
        DevMasterGraph._emit(state, "[PHASE_START] finalize_result")
        memory = DevMasterGraph._ensure_repository_memory(state)
        for path in [str(x) for x in state.get("touched_paths", []) if isinstance(x, str)]:
            DevMasterGraph._remember_text_value(state, "touched_paths", DevMasterGraph._relpath_safe(state, path))
        for err in [str(x) for x in state.get("errors", []) if isinstance(x, str)]:
            DevMasterGraph._remember_text_value(state, "errors", DevMasterGraph._sanitize_text(err, 320))
        for outcome in state.get("task_outcomes", []):
            if not isinstance(outcome, dict):
                continue
            cmd = str(outcome.get("command", "")).strip()
            if cmd:
                DevMasterGraph._remember_text_value(state, "attempted_commands", cmd)
        memory = DevMasterGraph._ensure_repository_memory(state)
        if state.get("bootstrap_status") == "failed":
            state["status"] = "bootstrap_failed"
        elif state.get("implementation_status") == "impl_skipped":
            state["status"] = "recoverable_blocked"
        elif state.get("validation_status") not in {"completed", "skipped"}:
            state["status"] = "recoverable_blocked"
        elif state.get("final_compile_status") != "completed":
            state["status"] = "partial_progress"
        elif not DevMasterGraph._all_mandatory_checklist_items_completed(state):
            state["status"] = "partial_progress"
            state["errors"].append("[CHECKLIST] mandatory items remain incomplete.")
        else:
            state["status"] = "completed"

        terminal_gate = DevMasterGraph._terminal_failure_gate(state)
        DevMasterGraph._emit_event(
            state,
            "terminal_failure_gate_entered",
            status_before=state.get("status", ""),
            gate=terminal_gate,
        )
        if state.get("status") not in {"completed", "bootstrap_failed"}:
            if bool(terminal_gate.get("approved", False)):
                state["status"] = "implementation_failed"
                DevMasterGraph._emit_event(
                    state,
                    "terminal_failure_gate_approved",
                    criterion=terminal_gate.get("criterion", "none"),
                    gate=terminal_gate,
                )
            else:
                DevMasterGraph._emit_event(
                    state,
                    "terminal_failure_gate_rejected",
                    criterion=terminal_gate.get("criterion", "none"),
                    gate=terminal_gate,
                )
        err_count = len(state.get("errors", []))
        checklist_total = len(state.get("internal_checklist", []))
        checklist_completed = len(
            [
                item
                for item in state.get("internal_checklist", [])
                if isinstance(item, dict) and str(item.get("status", "")) == "completed"
            ]
        )
        state["final_summary"] = (
            f"Developer master finished with status={state['status']} and errors={err_count}. "
            f"phase_status={state.get('phase_status', {})} "
            f"pass_status={state.get('implementation_pass_statuses', [])} "
            f"checklist={checklist_completed}/{checklist_total}"
        )
        outcomes = [x for x in state.get("task_outcomes", []) if isinstance(x, dict)]
        failed_outcomes = [x for x in outcomes if str(x.get("status", "")) != "completed"]
        candidate_attempts = memory.get("candidate_attempts", []) if isinstance(memory.get("candidate_attempts"), list) else []
        candidate_rejections = memory.get("candidate_rejections", []) if isinstance(memory.get("candidate_rejections"), list) else []
        validation_inference = memory.get("validation_inference", []) if isinstance(memory.get("validation_inference"), list) else []
        rejected_paths = {
            str(item.get("data", {}).get("candidate_path", "")).replace("\\", "/").strip().casefold()
            for item in candidate_rejections
            if isinstance(item, dict) and isinstance(item.get("data"), dict)
        }
        repeated_retry_count = 0
        total_attempt_count = 0
        for item in candidate_attempts:
            if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
                continue
            total_attempt_count += 1
            path = str(item.get("data", {}).get("candidate_path", "")).replace("\\", "/").strip().casefold()
            if path and path in rejected_paths:
                repeated_retry_count += 1
        bootstrap_outcomes = [
            o
            for o in outcomes
            if str(o.get("task_id", "")).startswith("bootstrap_") or str(o.get("task_id", "")).startswith("handoff_")
        ]
        reliability_metrics = {
            "target_selection_precision": (
                max(0.0, 1.0 - (len(candidate_rejections) / max(1, len(candidate_attempts))))
                if candidate_attempts
                else 1.0
            ),
            "repeated_failed_candidate_retry_rate": repeated_retry_count / max(1, total_attempt_count),
            "validation_executability_rate": (
                1.0
                if not validation_inference
                else (
                    sum(
                        1
                        for item in validation_inference
                        if isinstance(item, dict)
                        and isinstance(item.get("data"), dict)
                        and item.get("data", {}).get("classification") != "non_executable"
                    )
                    / max(1, len(validation_inference))
                )
            ),
            "compile_gate_execution_rate": 1.0 if state.get("final_compile_status") in {"completed", "failed"} else 0.0,
            "autonomous_recovery_success_rate": (
                1.0
                if any(str(x).startswith("final_compile_recovery_") for x in [o.get("task_id", "") for o in outcomes])
                and state.get("final_compile_status") == "completed"
                else 0.0
            ),
            "implementation_failed": 1.0 if state.get("status") == "implementation_failed" else 0.0,
            "task_failure_rate": len(failed_outcomes) / max(1, len(outcomes)),
            "bootstrap_true_success_rate": (
                sum(1 for o in bootstrap_outcomes if str(o.get("status", "")) == "completed") / max(1, len(bootstrap_outcomes))
                if bootstrap_outcomes
                else 1.0
            ),
            "semantic_recovery_acceptance_rate": (
                sum(
                    1
                    for event in state.get("telemetry_events", [])
                    if isinstance(event, dict) and str(event.get("category", "")) == "final_compile_recovery_started"
                )
                / max(
                    1,
                    sum(
                        1
                        for event in state.get("telemetry_events", [])
                        if isinstance(event, dict)
                        and str(event.get("category", "")) in {"recovery_intent_mismatch", "final_compile_recovery_started"}
                    ),
                )
            ),
            "implementation_cascade_failure_rate": (
                sum(
                    1
                    for item in state.get("internal_checklist", [])
                    if isinstance(item, dict)
                    and str(item.get("kind", "")) == "implementation"
                    and str(item.get("status", "")) == "failed"
                )
                / max(
                    1,
                    sum(
                        1
                        for item in state.get("internal_checklist", [])
                        if isinstance(item, dict) and str(item.get("kind", "")) == "implementation"
                    ),
                )
            ),
        }
        state["reliability_metrics"] = reliability_metrics
        DevMasterGraph._emit_event(
            state,
            "final_summary",
            status=state.get("status", "unknown"),
            errors=err_count,
            checklist_total=checklist_total,
            checklist_completed=checklist_completed,
            phase_status=state.get("phase_status", {}),
            implementation_passes=state.get("implementation_pass_statuses", []),
            task_outcomes=len(state.get("task_outcomes", [])),
            reliability_metrics=reliability_metrics,
        )
        DevMasterGraph._emit(state, f"[FINAL] {state['final_summary']}")
        DevMasterGraph._persist_run_artifacts(state)
        state["phase_status"]["finalize_result"] = "completed"
        return state
