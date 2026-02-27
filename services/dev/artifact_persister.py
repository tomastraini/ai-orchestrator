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


class ArtifactPersisterMixin:
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
