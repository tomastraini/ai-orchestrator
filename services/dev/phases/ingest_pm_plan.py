from __future__ import annotations

import hashlib
import json

from services.dev.types.dev_graph_state import DevGraphState
from shared.dev_schemas import derive_project_name


def run(state: DevGraphState, graph_cls: type) -> DevGraphState:
    state["current_step"] = "ingest_pm_plan"
    graph_cls._emit(state, "[PHASE_START] ingest_pm_plan")
    handoff = state.get("handoff") or {}
    continuation = handoff.get("continuation") if isinstance(handoff, dict) else {}
    if not isinstance(continuation, dict):
        continuation = {}
    if isinstance(handoff.get("task_outcomes"), list):
        state["task_outcomes"] = [x for x in handoff.get("task_outcomes", []) if isinstance(x, dict)]
    if isinstance(handoff.get("checklist_cursor"), str):
        state["checklist_cursor"] = str(handoff.get("checklist_cursor", ""))
    state["session_id"] = str(continuation.get("session_id", "")).strip()
    state["parent_request_id"] = str(continuation.get("parent_request_id", "")).strip()
    state["iteration_index"] = int(continuation.get("iteration_index", 0) or 0)
    state["continuation_reason"] = str(continuation.get("continuation_reason", "initial")).strip() or "initial"
    state["delta_requirement"] = str(continuation.get("delta_requirement", "")).strip()
    state["prior_run_summary"] = str(continuation.get("prior_run_summary", "")).strip()
    state["carry_forward_memory"] = bool(continuation.get("carry_forward_memory", True))
    state["trigger_type"] = str(continuation.get("trigger_type", "initial")).strip() or "initial"
    state["continuation_mode"] = str(continuation.get("continuation_mode", "always")).strip() or "always"
    state["continuation_guidance"] = (
        dict(continuation.get("continuation_guidance", {}))
        if isinstance(continuation.get("continuation_guidance"), dict)
        else {}
    )
    project_root = handoff.get("project_root")
    if isinstance(project_root, str) and "/" in project_root:
        normalized_root = project_root.replace("\\", "/").strip().lstrip("./")
        state["project_root"] = normalized_root
        state["project_name"] = normalized_root.rstrip("/").split("/")[-1]
    else:
        project_name = derive_project_name(state["plan"])
        state["project_name"] = project_name
        state["project_root"] = f"projects/{project_name}"
    graph_cls._emit(state, f"[INGEST] project='{state['project_name']}'")
    plan = state.get("plan", {})
    plan_hash = hashlib.sha1(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    graph_cls._emit_event(
        state,
        "plan_ingest",
        project_name=state["project_name"],
        project_root=state["project_root"],
        plan_hash=plan_hash,
        bootstrap_commands_count=len(plan.get("bootstrap_commands", [])) if isinstance(plan, dict) else 0,
        target_files_count=len(plan.get("target_files", [])) if isinstance(plan, dict) else 0,
        validation_count=len(plan.get("validation", [])) if isinstance(plan, dict) else 0,
    )
    state["phase_status"]["ingest_pm_plan"] = "completed"
    return state

