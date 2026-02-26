from __future__ import annotations

from typing import Dict, List

from services.dev.types.dev_graph_state import DevGraphState
from shared.dev_schemas import DevTask


def run(state: DevGraphState, graph_cls: type) -> DevGraphState:
    state["current_step"] = "derive_dev_todos"
    graph_cls._emit(state, "[PHASE_START] derive_dev_todos")
    plan = state["plan"]
    handoff = state.get("handoff") or {}
    bootstrap_tasks: List[DevTask] = []
    validation_tasks: List[DevTask] = []
    implementation_targets: List[Dict[str, str]] = []

    handoff_steps = handoff.get("execution_steps")
    if isinstance(handoff_steps, list) and len(handoff_steps) > 0:
        for i, cmd in enumerate(handoff_steps, start=1):
            if isinstance(cmd, dict):
                bootstrap_tasks.append(
                    DevTask(
                        id=f"handoff_{i}",
                        description=str(cmd.get("purpose", "handoff step")),
                        command=str(cmd.get("command", "")),
                        cwd=str(cmd.get("cwd", ".")),
                        kind="bootstrap",
                    )
                )
    else:
        for i, cmd in enumerate(plan.get("bootstrap_commands", []), start=1):
            if isinstance(cmd, dict):
                bootstrap_tasks.append(
                    DevTask(
                        id=f"bootstrap_{i}",
                        description=str(cmd.get("purpose", "bootstrap step")),
                        command=str(cmd.get("command", "")),
                        cwd=str(cmd.get("cwd", ".")),
                        kind="bootstrap",
                    )
                )

    for i, validation in enumerate(plan.get("validation", []), start=1):
        if isinstance(validation, str):
            validation_tasks.append(
                DevTask(
                    id=f"validation_{i}",
                    description=validation,
                    command=None,
                    cwd=".",
                    kind="validation",
                )
            )

    for target in plan.get("target_files", []):
        if not isinstance(target, dict):
            continue
        implementation_targets.append(
            {
                "file_name": str(target.get("file_name", "")),
                "expected_path_hint": str(target.get("expected_path_hint", "")),
                "modification_type": str(target.get("modification_type", "")),
                "details": str(target.get("details", "")),
                "creation_policy": str(target.get("creation_policy", "")),
            }
        )

    state["bootstrap_tasks"] = bootstrap_tasks
    state["validation_tasks"] = validation_tasks
    state["implementation_targets"] = implementation_targets
    graph_cls._emit(
        state,
        "[TODO] bootstrap_tasks="
        f"{len(bootstrap_tasks)} implementation_targets={len(implementation_targets)} "
        f"validation_tasks={len(validation_tasks)}",
    )
    graph_cls._emit_event(
        state,
        "todo_derivation",
        bootstrap_tasks=len(bootstrap_tasks),
        implementation_targets=len(implementation_targets),
        validation_tasks=len(validation_tasks),
        restored_from_handoff=bool(isinstance(handoff_steps, list) and len(handoff_steps) > 0),
    )
    state["phase_status"]["derive_dev_todos"] = "completed"
    return state

