from __future__ import annotations

from services.dev.types.dev_graph_state import DevGraphState


def run(state: DevGraphState, graph_cls: type) -> DevGraphState:
    state["current_step"] = "finalize_result"
    graph_cls._emit(state, "[PHASE_START] finalize_result")
    if state.get("status") in {"bootstrap_failed", "implementation_failed"}:
        pass
    elif state.get("implementation_status") == "impl_skipped":
        state["status"] = "bootstrap_failed"
    elif state.get("validation_status") not in {"completed", "skipped"}:
        state["status"] = "implementation_failed"
    elif state.get("final_compile_status") != "completed":
        state["status"] = "implementation_failed"
    elif not graph_cls._all_mandatory_checklist_items_completed(state):
        state["status"] = "implementation_failed"
        state["errors"].append("[CHECKLIST] mandatory items remain incomplete.")
    else:
        state["status"] = "completed"
    err_count = len(state.get("errors", []))
    checklist_total = len(state.get("internal_checklist", []))
    checklist_completed = len(
        [
            item
            for item in state.get("internal_checklist", [])
            if isinstance(item, dict) and str(item.get("status", "")) == "completed"
        ]
    )
    state["final_summary"] = (
        f"Developer master finished with status={state['status']} and errors={err_count}. "
        f"phase_status={state.get('phase_status', {})} "
        f"pass_status={state.get('implementation_pass_statuses', [])} "
        f"checklist={checklist_completed}/{checklist_total}"
    )
    memory = state.get("repository_memory", {}) if isinstance(state.get("repository_memory"), dict) else {}
    attempted = memory.get("attempted_commands", [])
    known_errors = memory.get("errors", [])
    touched = memory.get("touched_paths", [])
    if not isinstance(attempted, list):
        attempted = []
    if not isinstance(known_errors, list):
        known_errors = []
    if not isinstance(touched, list):
        touched = []
    attempted.extend([str(outcome.get("command", "")) for outcome in state.get("task_outcomes", []) if isinstance(outcome, dict)])
    known_errors.extend([str(err) for err in state.get("errors", [])])
    touched.extend([str(path) for path in state.get("touched_paths", [])])
    state["repository_memory"] = {
        "attempted_commands": attempted[-200:],
        "errors": known_errors[-200:],
        "touched_paths": touched[-500:],
    }
    graph_cls._emit_event(
        state,
        "final_summary",
        status=state.get("status", "unknown"),
        errors=err_count,
        checklist_total=checklist_total,
        checklist_completed=checklist_completed,
        phase_status=state.get("phase_status", {}),
        implementation_passes=state.get("implementation_pass_statuses", []),
        task_outcomes=len(state.get("task_outcomes", [])),
        memory_stats={
            "attempted_commands": len(state["repository_memory"].get("attempted_commands", [])),
            "errors": len(state["repository_memory"].get("errors", [])),
            "touched_paths": len(state["repository_memory"].get("touched_paths", [])),
        },
    )
    graph_cls._emit(state, f"[FINAL] {state['final_summary']}")
    state["phase_status"]["finalize_result"] = "completed"
    return state

