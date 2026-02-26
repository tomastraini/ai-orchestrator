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
    state["phase_status"]["prepare_execution_steps"] = "completed"
    return state

