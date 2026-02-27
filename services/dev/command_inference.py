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


class CommandInferenceMixin:
    @staticmethod
    def _project_requires_standardized_setup(project_dir: str, implementation_targets: List[Dict[str, Any]]) -> bool:
        if not os.path.isdir(project_dir):
            return False
        target_count = len([x for x in implementation_targets if isinstance(x, dict)])
        if target_count <= 0:
            return False
        try:
            top_entries = [x for x in os.listdir(project_dir) if not str(x).startswith(".")]
        except Exception:
            top_entries = []
        sparse_root = len(top_entries) <= 8
        has_dependency_state = os.path.exists(os.path.join(project_dir, "node_modules")) or os.path.exists(
            os.path.join(project_dir, ".venv")
        )
        return sparse_root and not has_dependency_state and target_count >= 3

    @staticmethod
    def _infer_standardized_setup_commands(project_dir: str) -> List[str]:
        commands: List[str] = []
        candidates = DevMasterGraph._discover_repository_command_candidates(project_dir)
        setup_candidates = candidates.get("setup", []) if isinstance(candidates, dict) else []
        for command in setup_candidates:
            cmd = str(command).strip()
            if not cmd:
                continue
            executable, _ = DevMasterGraph._is_validation_command_executable(cmd, project_dir=project_dir)
            if executable and cmd not in commands:
                commands.append(cmd)
        return commands

    @staticmethod
    def _infer_bootstrap_tasks_from_intent(state: DevGraphState) -> List[DevTask]:
        if not isinstance(state.get("logs"), list):
            state["logs"] = []
        if not isinstance(state.get("telemetry_events"), list):
            state["telemetry_events"] = []
        scope_root = str(state.get("scope_root", "")).strip()
        project_root = str(state.get("project_root", "")).strip()
        rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
        project_dir = os.path.join(scope_root, rel) if scope_root and rel else ""
        implementation_targets = state.get("implementation_targets", [])
        if not project_dir or not DevMasterGraph._project_requires_standardized_setup(
            project_dir, implementation_targets if isinstance(implementation_targets, list) else []
        ):
            DevMasterGraph._emit_event(
                state,
                "setup_strategy_rejected",
                strategy="standardized_setup",
                reason="project_not_sparse_or_targets_insufficient",
            )
            return []

        setup_commands = DevMasterGraph._infer_standardized_setup_commands(project_dir)
        if not setup_commands:
            DevMasterGraph._emit_event(
                state,
                "setup_strategy_rejected",
                strategy="standardized_setup",
                reason="no_safe_setup_command_inferred",
            )
            return []

        tasks = [
            DevTask(
                id=f"bootstrap_setup_{idx + 1}",
                description=f"run standardized setup command: {command}",
                command=command,
                cwd=project_root or ".",
                kind="bootstrap",
            )
            for idx, command in enumerate(setup_commands)
        ]
        DevMasterGraph._emit_event(
            state,
            "setup_strategy_selected",
            strategy="standardized_setup",
            setup_strategy_candidates=setup_commands,
            setup_strategy_reason="project_sparse_and_setup_command_available",
        )
        return tasks

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
