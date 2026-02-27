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


class PhaseOrchestrationMixin:
    @staticmethod
    def _ingest_pm_plan(state: DevGraphState) -> DevGraphState:
        return ingest_pm_plan_phase(state, DevMasterGraph)

    @staticmethod
    def _derive_dev_todos(state: DevGraphState) -> DevGraphState:
        return derive_dev_todos_phase(state, DevMasterGraph)

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
