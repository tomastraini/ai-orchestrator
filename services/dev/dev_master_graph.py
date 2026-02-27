from __future__ import annotations

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
        continuation_handoff = (
            handoff.get("continuation", {})
            if isinstance(handoff, dict) and isinstance(handoff.get("continuation"), dict)
            else {}
        )
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
            "pending_tasks": [],
            "session_id": str(continuation_handoff.get("session_id", "")).strip(),
            "parent_request_id": str(continuation_handoff.get("parent_request_id", "")).strip(),
            "iteration_index": int(continuation_handoff.get("iteration_index", 0) or 0),
            "continuation_reason": str(continuation_handoff.get("continuation_reason", "initial")).strip() or "initial",
            "delta_requirement": str(continuation_handoff.get("delta_requirement", "")).strip(),
            "prior_run_summary": str(continuation_handoff.get("prior_run_summary", "")).strip(),
            "carry_forward_memory": bool(continuation_handoff.get("carry_forward_memory", True)),
            "continuation_eligible": False,
            "ready_for_followup": False,
            "continuation_mode": str(continuation_handoff.get("continuation_mode", "always")).strip() or "always",
            "trigger_type": str(continuation_handoff.get("trigger_type", "initial")).strip() or "initial",
            "continuation_guidance": {},
            "needs_validation_clarification": False,
            "validation_followup_options": [],
            "browser_validation_adapter": handoff.get("browser_validation_adapter") if isinstance(handoff, dict) else None,
            "validation_evidence": [],
        }
        initial_state["repository_memory"] = DevMasterGraph._initialize_repository_memory(initial_state, handoff)
        DevMasterGraph._emit_event(
            initial_state,
            "memory_carried_forward" if bool(initial_state.get("carry_forward_memory", True)) else "memory_reset",
            carry_forward_memory=bool(initial_state.get("carry_forward_memory", True)),
            iteration_index=int(initial_state.get("iteration_index", 0) or 0),
        )
        result = self._compiled_graph.invoke(initial_state)
        result.pop("ask_user", None)
        result.pop("llm_corrector", None)
        result.pop("log_sink", None)
        return result


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
