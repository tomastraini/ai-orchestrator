from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from shared.dev_schemas import DevTask


class DevGraphState(TypedDict, total=False):
    request_id: str
    plan: Dict[str, Any]
    handoff: Dict[str, Any]
    scope_root: str
    project_name: str
    project_root: str
    active_project_root: str
    detected_stacks: List[str]
    dev_preflight_plan: Dict[str, Any]
    status: str
    current_step: str
    bootstrap_tasks: List[DevTask]
    implementation_targets: List[Dict[str, str]]
    validation_tasks: List[DevTask]
    clarifications: List[Dict[str, str]]
    logs: List[str]
    touched_paths: List[str]
    errors: List[str]
    final_summary: str
    ask_user: Any
    llm_corrector: Any
    retry_count: int
    max_retries: int
    last_error: str
    attempt_history: List[Dict[str, Any]]
    bootstrap_status: str
    implementation_status: str
    llm_calls_used: int
    llm_call_budget: int
    phase_status: Dict[str, str]
    implementation_pass_statuses: List[str]
    log_sink: Any
    validation_status: str
    root_resolution_evidence: Dict[str, Any]
    llm_context_contract: Dict[str, Any]
    internal_checklist: List[Dict[str, Any]]
    checklist_index: Dict[str, int]
    final_compile_tasks: List[DevTask]
    final_compile_status: str
    task_outcomes: List[Dict[str, Any]]
    checklist_cursor: str
    telemetry_events: List[Dict[str, Any]]
    dev_discovery_candidates: List[Dict[str, Any]]
    dev_technical_plan: Dict[str, Any]
    dev_plan_approved: bool

