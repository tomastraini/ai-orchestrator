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


class ErrorClassifierMixin:
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
