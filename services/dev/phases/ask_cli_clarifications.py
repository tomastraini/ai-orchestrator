from __future__ import annotations

from services.dev.types.dev_graph_state import DevGraphState


def run(state: DevGraphState, graph_cls: type) -> DevGraphState:
    state["current_step"] = "ask_cli_clarifications_if_needed"
    graph_cls._emit(state, "[PHASE_START] ask_cli_clarifications_if_needed")
    plan = state["plan"]
    ask_user = state.get("ask_user")

    if not callable(ask_user):
        graph_cls._emit(state, "[CLARIFY] no CLI callback provided")
        state["phase_status"]["ask_cli_clarifications_if_needed"] = "completed"
        return state

    project_mode = plan.get("project_mode")
    path_hint = None
    project_ref = plan.get("project_ref")
    if isinstance(project_ref, dict):
        path_hint = project_ref.get("path_hint")

    if project_mode == "existing_project" and not path_hint:
        question = (
            "Developer needs path for existing project. "
            "Where inside ./projects should work happen?"
        )
        answer = ask_user(question).strip()
        state["clarifications"].append({"question": question, "answer": answer})
        graph_cls._emit(state, "[CLARIFY] existing project path clarified via CLI")
    else:
        graph_cls._emit(state, "[CLARIFY] no additional questions needed")
    state["phase_status"]["ask_cli_clarifications_if_needed"] = "completed"
    return state

