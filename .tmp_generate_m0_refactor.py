from __future__ import annotations

import ast
from pathlib import Path


def main() -> None:
    root = Path("services/dev")
    src_path = root / "dev_master_graph.py"
    source_snapshot = Path(".tmp_original_dev_master_graph.py")
    src = (
        source_snapshot.read_text(encoding="utf-8-sig")
        if source_snapshot.exists()
        else src_path.read_text(encoding="utf-8")
    )
    lines = src.splitlines()
    tree = ast.parse(src)
    cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DevMasterGraph"][0]
    methods = {n.name: (n.lineno, n.end_lineno) for n in cls.body if isinstance(n, ast.FunctionDef)}

    imports = """from __future__ import annotations

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
"""

    def get_method(name: str) -> str:
        start, end = methods[name]
        if start > 1 and lines[start - 2].strip() == "@staticmethod":
            start -= 1
        return "\n".join(lines[start - 1 : end])

    modules = {
        "state_utilities.py": (
            "StateUtilitiesMixin",
            [
                "_emit",
                "_sanitize_text",
                "_relpath_safe",
                "_emit_event",
                "_detect_stacks_for_root",
                "_default_validation_commands",
                "_is_long_running_validation_command",
                "_extract_validation_command",
            ],
        ),
        "repository_memory.py": (
            "RepositoryMemoryMixin",
            [
                "_default_repository_memory",
                "_initialize_repository_memory",
                "_merge_repository_memory",
                "_workspace_changed_significantly",
                "_trim_repository_memory",
                "_ensure_repository_memory",
                "_remember",
                "_remember_text_value",
                "_record_error_file_refs",
                "_has_recent_candidate_rejection",
            ],
        ),
        "checklist_manager.py": (
            "ChecklistManagerMixin",
            [
                "_reindex_checklist",
                "_upsert_checklist_item",
                "_find_checklist_item",
                "_append_item_evidence",
                "_set_checklist_status",
                "_next_actionable_checklist_item",
                "_all_mandatory_checklist_items_completed",
                "_build_internal_checklist",
                "_reconcile_checklist_item",
            ],
        ),
        "command_inference.py": (
            "CommandInferenceMixin",
            [
                "_project_requires_standardized_setup",
                "_infer_standardized_setup_commands",
                "_infer_bootstrap_tasks_from_intent",
                "_read_package_scripts",
                "_infer_node_runner",
                "_discover_repository_command_candidates",
                "_is_validation_command_executable",
                "_bootstrap_artifact_gate",
                "_infer_final_compile_commands",
            ],
        ),
        "error_classifier.py": (
            "ErrorClassifierMixin",
            [
                "_recovery_satisfies_task_intent",
                "_extract_deterministic_failure_signatures",
                "_terminal_failure_gate",
                "_extract_error_file_refs",
                "_classify_diagnostic_taxonomy",
                "_infer_compile_recovery_commands",
            ],
        ),
        "project_index_manager.py": (
            "ProjectIndexManagerMixin",
            [
                "_build_dev_technical_plan",
                "_build_llm_context_contract",
                "_build_active_root_file_index",
                "_emit_index_snapshot",
                "_refresh_active_root_index",
            ],
        ),
        "target_resolver.py": (
            "TargetResolverMixin",
            [
                "_compute_discovery_candidates",
                "_is_within_scope",
                "_has_project_marker",
                "_source_hint_count",
                "_normalize_target_tail",
                "_resolve_active_project_root_after_bootstrap",
                "_normalize_expected_suffix_for_active_root",
                "_choose_best_index_candidate",
                "_resolve_target_file_path",
                "_discover_existing_path",
            ],
        ),
        "content_generator.py": (
            "ContentGeneratorMixin",
            [
                "_comment_for_path",
                "_component_name_from_file",
                "_default_generated_content",
                "_infer_rename_destination",
                "_llm_generate_file_content",
            ],
        ),
        "implementation_executor.py": (
            "ImplementationExecutorMixin",
            [
                "_file_sha1",
                "_execute_bootstrap_phase_impl",
                "_resolve_implementation_path",
            ],
        ),
        "implementation_target_runner.py": (
            "ImplementationTargetRunnerMixin",
            [
                "_apply_target_in_pass",
                "_run_implementation_review",
                "_execute_implementation_phase_impl",
            ],
        ),
        "validation_executor.py": (
            "ValidationExecutorMixin",
            [
                "_execute_validation_phase_impl",
            ],
        ),
        "final_compile_gate.py": (
            "FinalCompileGateMixin",
            [
                "_execute_final_compile_gate_impl",
            ],
        ),
        "artifact_persister.py": (
            "ArtifactPersisterMixin",
            [
                "_persist_run_artifacts",
            ],
        ),
        "finalize_result_handler.py": (
            "FinalizeResultHandlerMixin",
            [
                "_finalize_result_impl",
            ],
        ),
        "phase_orchestration.py": (
            "PhaseOrchestrationMixin",
            [
                "_ingest_pm_plan",
                "_derive_dev_todos",
                "_dev_preflight_planning",
                "_ask_cli_clarifications_if_needed",
                "_prepare_execution_steps",
                "_execute_bootstrap_phase",
                "_execute_implementation_phase",
                "_execute_validation_phase",
                "_execute_final_compile_gate",
                "_finalize_result",
            ],
        ),
    }

    for file_name, (class_name, method_names) in modules.items():
        body = "\n\n".join(get_method(name) for name in method_names)
        content = f"{imports}\n\nclass {class_name}:\n{body}\n"
        (root / file_name).write_text(content, encoding="utf-8")

    init_src = get_method("__init__")
    run_src = get_method("run")
    shell = f"""from __future__ import annotations

from typing import Any, Callable, Dict

from langgraph.graph import END, START, StateGraph

import services.dev.artifact_persister as _artifact_mod
import services.dev.checklist_manager as _checklist_mod
import services.dev.command_inference as _command_mod
import services.dev.content_generator as _content_mod
import services.dev.error_classifier as _error_mod
import services.dev.final_compile_gate as _compile_mod
import services.dev.finalize_result_handler as _finalize_mod
import services.dev.implementation_executor as _impl_mod
import services.dev.implementation_target_runner as _impl_target_mod
import services.dev.phase_orchestration as _phase_mod
import services.dev.project_index_manager as _index_mod
import services.dev.repository_memory as _memory_mod
import services.dev.state_utilities as _state_mod
import services.dev.target_resolver as _resolver_mod
import services.dev.validation_executor as _validation_mod
from services.dev.types.dev_graph_state import DevGraphState


DevAskFn = Callable[[str], str]
LLMCorrectorFn = Callable[[Dict[str, Any]], str]


class DevMasterGraph(
    _state_mod.StateUtilitiesMixin,
    _memory_mod.RepositoryMemoryMixin,
    _checklist_mod.ChecklistManagerMixin,
    _command_mod.CommandInferenceMixin,
    _error_mod.ErrorClassifierMixin,
    _index_mod.ProjectIndexManagerMixin,
    _resolver_mod.TargetResolverMixin,
    _content_mod.ContentGeneratorMixin,
    _impl_mod.ImplementationExecutorMixin,
    _impl_target_mod.ImplementationTargetRunnerMixin,
    _validation_mod.ValidationExecutorMixin,
    _compile_mod.FinalCompileGateMixin,
    _artifact_mod.ArtifactPersisterMixin,
    _finalize_mod.FinalizeResultHandlerMixin,
    _phase_mod.PhaseOrchestrationMixin,
):
    MEMORY_LIMITS = {{
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
    }}
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
    INDEX_IGNORE_DIRS = {{"node_modules", ".git", ".venv", "__pycache__", "dist", "build", ".next", ".cache"}}

{init_src}

{run_src}


for _module in [
    _state_mod,
    _memory_mod,
    _checklist_mod,
    _command_mod,
    _error_mod,
    _index_mod,
    _resolver_mod,
    _content_mod,
    _impl_mod,
    _impl_target_mod,
    _validation_mod,
    _compile_mod,
    _artifact_mod,
    _finalize_mod,
    _phase_mod,
]:
    setattr(_module, "DevMasterGraph", DevMasterGraph)
"""
    src_path.write_text(shell, encoding="utf-8")


if __name__ == "__main__":
    main()
