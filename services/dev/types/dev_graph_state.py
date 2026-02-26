from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from shared.dev_schemas import DevTask


class ValidationEvidence(TypedDict, total=False):
    strategy: str
    notes: str
    steps: List[str]
    observations: List[str]


class DevMemoryEntry(TypedDict, total=False):
    timestamp_ms: int
    phase: str
    kind: str
    source_request_id: str
    iteration_index: int
    data: Dict[str, Any]


class DevRepositoryMemory(TypedDict, total=False):
    files_inspected: List[DevMemoryEntry]
    symbols_discovered: List[DevMemoryEntry]
    assumptions: List[DevMemoryEntry]
    candidate_attempts: List[DevMemoryEntry]
    candidate_rejections: List[DevMemoryEntry]
    correction_attempts: List[DevMemoryEntry]
    command_failures: List[DevMemoryEntry]
    diagnostic_file_refs: List[str]
    validation_inference: List[DevMemoryEntry]
    attempted_commands: List[str]
    errors: List[str]
    touched_paths: List[str]


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
    active_root_file_index: Dict[str, Any]
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
    repository_memory: DevRepositoryMemory
    target_resolution_evidence: Dict[str, Any]
    capability_gaps: List[Dict[str, Any]]
    reliability_metrics: Dict[str, Any]
    pending_tasks: List[str]
    session_id: str
    parent_request_id: str
    iteration_index: int
    continuation_reason: str
    delta_requirement: str
    prior_run_summary: str
    carry_forward_memory: bool
    continuation_eligible: bool
    ready_for_followup: bool
    continuation_mode: str
    trigger_type: str
    continuation_guidance: Dict[str, Any]
    needs_validation_clarification: bool
    validation_followup_options: List[Dict[str, str]]
    browser_validation_adapter: Any
    validation_evidence: List[ValidationEvidence]

