from __future__ import annotations

import os
import platform
from typing import List

from services.dev.types.dev_graph_state import DevGraphState
from shared.dev_schemas import DevTask


def run(state: DevGraphState, graph_cls: type) -> DevGraphState:
    state["current_step"] = "dev_preflight_planning"
    graph_cls._emit(state, "[PHASE_START] dev_preflight_planning")
    project_root = str(state.get("project_root", f"projects/{state.get('project_name', 'project')}"))
    rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
    project_dir = os.path.join(state["scope_root"], rel)
    os.makedirs(project_dir, exist_ok=True)
    detected = graph_cls._detect_stacks_for_root(project_dir)
    state["detected_stacks"] = detected
    state["active_project_root"] = project_dir

    raw_validation_requirements = [
        str(x).strip()
        for x in state.get("plan", {}).get("validation", [])
        if isinstance(x, str) and str(x).strip()
    ]
    validation_commands: List[str] = []
    unresolved_validation_requirements: List[dict] = []
    for requirement in raw_validation_requirements:
        cmd = graph_cls._extract_validation_command(
            requirement,
            stacks=detected,
            project_dir=project_dir,
        )
        if cmd:
            lower_cmd = cmd.lower().strip()
            if any(lower_cmd.startswith(prefix) for prefix in ["npm ", "pnpm ", "yarn "]):
                package_json = os.path.join(project_dir, "package.json")
                if not os.path.exists(package_json):
                    unresolved_validation_requirements.append(
                        {
                            "requirement": requirement,
                            "classification": "manual_followup_required",
                            "reason": "missing_package_json",
                        }
                    )
                    continue
            if hasattr(graph_cls, "_is_validation_command_executable"):
                executable, reason = graph_cls._is_validation_command_executable(cmd, project_dir=project_dir)
                if not executable:
                    unresolved_validation_requirements.append(
                        {
                            "requirement": requirement,
                            "command": cmd,
                            "classification": "manual_followup_required",
                            "reason": reason,
                        }
                    )
                    continue
            validation_commands.append(cmd)
        else:
            unresolved_validation_requirements.append(
                {
                    "requirement": requirement,
                    "classification": "manual_followup_required",
                    "reason": "non_executable_requirement",
                }
            )

    if not validation_commands and not raw_validation_requirements:
        validation_commands = graph_cls._default_validation_commands(detected)
    final_compile_commands = graph_cls._infer_final_compile_commands(
        project_dir=project_dir,
        stacks=detected,
        validation_commands=validation_commands,
    )

    state["dev_preflight_plan"] = {
        "os": platform.system(),
        "active_project_root": project_dir,
        "detected_stacks": detected,
        "validation_commands": validation_commands,
        "final_compile_commands": final_compile_commands,
        "raw_validation_requirements": raw_validation_requirements,
        "unresolved_validation_requirements": unresolved_validation_requirements,
    }
    state["validation_tasks"] = [
        DevTask(
            id=f"validation_cmd_{idx+1}",
            description=f"run validation command: {cmd}",
            command=cmd,
            cwd=project_root,
            kind="validation",
        )
        for idx, cmd in enumerate(validation_commands)
    ]
    state["final_compile_tasks"] = [
        DevTask(
            id=f"final_compile_{idx+1}",
            description=f"run final compile gate: {cmd}",
            command=cmd,
            cwd=project_root,
            kind="validation",
        )
        for idx, cmd in enumerate(final_compile_commands)
    ]
    graph_cls._build_internal_checklist(state)
    graph_cls._emit(
        state,
        f"[PREFLIGHT] os={state['dev_preflight_plan']['os']} stacks={detected} active_root={project_dir}",
    )
    if validation_commands:
        graph_cls._emit(state, f"[PREFLIGHT] validation_commands={validation_commands}")
    else:
        graph_cls._emit(state, "[PREFLIGHT] no executable validation commands inferred")
    if unresolved_validation_requirements:
        graph_cls._emit(
            state,
            f"[PREFLIGHT] unresolved_validation_requirements={unresolved_validation_requirements}",
        )
    if final_compile_commands:
        graph_cls._emit(state, f"[PREFLIGHT] final_compile_commands={final_compile_commands}")
    else:
        graph_cls._emit(state, "[PREFLIGHT] no terminating compile commands inferred")
    graph_cls._emit_event(
        state,
        "preflight_validation_inference",
        os=state["dev_preflight_plan"]["os"],
        active_project_root=graph_cls._relpath_safe(state, project_dir),
        detected_stacks=detected,
        validation_commands=validation_commands,
        unresolved_validation_requirements=unresolved_validation_requirements,
        final_compile_commands=final_compile_commands,
    )
    state["phase_status"]["dev_preflight_planning"] = "completed"
    graph_cls._remember(
        state,
        "validation_inference",
        {
            "validation_commands": validation_commands,
            "unresolved_validation_requirements": unresolved_validation_requirements,
            "raw_validation_requirements": raw_validation_requirements,
            "detected_stacks": detected,
        },
    )
    return state

