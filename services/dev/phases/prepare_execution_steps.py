from __future__ import annotations

import os

from services.dev.types.dev_graph_state import DevGraphState


def run(state: DevGraphState, graph_cls: type) -> DevGraphState:
    state["current_step"] = "prepare_execution_steps"
    graph_cls._emit(state, "[PHASE_START] prepare_execution_steps")
    project_root = str(state.get("project_root", f"projects/{state.get('project_name', 'project')}"))
    rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
    project_dir = os.path.join(state["scope_root"], rel)
    os.makedirs(project_dir, exist_ok=True)
    state["touched_paths"].append(project_dir)
    graph_cls._emit(state, f"[PREPARE] ensured project dir {project_dir}")
    graph_cls._compute_discovery_candidates(state)
    technical_plan = graph_cls._build_dev_technical_plan(state)
    graph_cls._emit(state, "[DEV_PLAN] technical plan generated")
    graph_cls._emit(
        state,
        f"[DEV_PLAN] affected_files={len(technical_plan.get('affected_files', []))} "
        f"commands={len(technical_plan.get('command_plan', []))} "
        f"todos={len(technical_plan.get('todo_plan', []))}",
    )
    ask_user = state.get("ask_user")
    if callable(ask_user):
        file_lines = [
            f"- {item.get('change_type', 'modify')}: {item.get('path_hint', item.get('file_name', ''))}"
            for item in technical_plan.get("affected_files", [])[:25]
        ]
        todo_lines = [
            f"- {todo.get('id')}: {todo.get('description')}"
            for todo in technical_plan.get("todo_plan", [])[:25]
        ]
        approval_question = (
            "Developer technical plan is ready.\n"
            "Affected files:\n"
            + ("\n".join(file_lines) if file_lines else "- (none listed)")
            + "\nTodos:\n"
            + ("\n".join(todo_lines) if todo_lines else "- (none listed)")
            + "\nProceed with execution? Reply yes to continue, anything else to abort."
        )
        answer = str(ask_user(approval_question)).strip().lower()
        approved = answer not in {"n", "no", "false", "0", "reject", "deny", "stop", "cancel"}
        state["dev_plan_approved"] = approved
        state["clarifications"].append({"question": approval_question, "answer": answer})
        graph_cls._emit_event(
            state,
            "dev_plan_approval",
            approved=approved,
            answer=answer,
            technical_plan=technical_plan,
        )
        if not approved:
            state["errors"].append("[DEV_PLAN] execution aborted: technical plan not approved by user.")
            state["status"] = "implementation_failed"
            state["phase_status"]["prepare_execution_steps"] = "failed"
            return state
    else:
        state["dev_plan_approved"] = True
        graph_cls._emit(state, "[DEV_PLAN] no CLI callback provided; auto-approved in non-interactive mode")
    state["phase_status"]["prepare_execution_steps"] = "completed"
    return state

