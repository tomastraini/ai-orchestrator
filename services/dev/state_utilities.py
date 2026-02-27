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


class StateUtilitiesMixin:
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
