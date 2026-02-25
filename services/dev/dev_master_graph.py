from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from services.dev.dev_executor import execute_dev_tasks
from shared.dev_schemas import DevTask, derive_project_name


DevAskFn = Callable[[str], str]


class DevGraphState(TypedDict, total=False):
    request_id: str
    plan: Dict[str, Any]
    handoff: Dict[str, Any]
    scope_root: str
    project_name: str
    status: str
    current_step: str
    tasks: List[DevTask]
    clarifications: List[Dict[str, str]]
    logs: List[str]
    touched_paths: List[str]
    errors: List[str]
    final_summary: str
    ask_user: Any


class DevMasterGraph:
    def __init__(self) -> None:
        graph = StateGraph(DevGraphState)
        graph.add_node("ingest_pm_plan", self._ingest_pm_plan)
        graph.add_node("derive_dev_todos", self._derive_dev_todos)
        graph.add_node("ask_cli_clarifications_if_needed", self._ask_cli_clarifications_if_needed)
        graph.add_node("prepare_execution_steps", self._prepare_execution_steps)
        graph.add_node("execute_steps_in_sandbox", self._execute_steps_in_sandbox)
        graph.add_node("finalize_result", self._finalize_result)

        graph.add_edge(START, "ingest_pm_plan")
        graph.add_edge("ingest_pm_plan", "derive_dev_todos")
        graph.add_edge("derive_dev_todos", "ask_cli_clarifications_if_needed")
        graph.add_edge("ask_cli_clarifications_if_needed", "prepare_execution_steps")
        graph.add_edge("prepare_execution_steps", "execute_steps_in_sandbox")
        graph.add_edge("execute_steps_in_sandbox", "finalize_result")
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
    ) -> Dict[str, Any]:
        initial_state: DevGraphState = {
            "request_id": request_id,
            "plan": plan,
            "handoff": handoff or {},
            "scope_root": scope_root,
            "status": "running",
            "current_step": "init",
            "tasks": [],
            "clarifications": [],
            "logs": [],
            "touched_paths": [],
            "errors": [],
            "ask_user": ask_user,
        }
        result = self._compiled_graph.invoke(initial_state)
        result.pop("ask_user", None)
        return result

    @staticmethod
    def _ingest_pm_plan(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "ingest_pm_plan"
        handoff = state.get("handoff") or {}
        project_root = handoff.get("project_root")
        if isinstance(project_root, str) and "/" in project_root:
            state["project_name"] = project_root.replace("\\", "/").rstrip("/").split("/")[-1]
        else:
            state["project_name"] = derive_project_name(state["plan"])
        state["logs"].append(f"[INGEST] project='{state['project_name']}'")
        return state

    @staticmethod
    def _derive_dev_todos(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "derive_dev_todos"
        plan = state["plan"]
        handoff = state.get("handoff") or {}
        tasks: List[DevTask] = []

        handoff_steps = handoff.get("execution_steps")
        if isinstance(handoff_steps, list) and len(handoff_steps) > 0:
            for i, cmd in enumerate(handoff_steps, start=1):
                if isinstance(cmd, dict):
                    tasks.append(
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
                    tasks.append(
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
                tasks.append(
                    DevTask(
                        id=f"validation_{i}",
                        description=validation,
                        command=None,
                        cwd=".",
                        kind="validation",
                    )
                )

        state["tasks"] = tasks
        state["logs"].append(f"[TODO] derived_tasks={len(tasks)}")
        return state

    @staticmethod
    def _ask_cli_clarifications_if_needed(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "ask_cli_clarifications_if_needed"
        plan = state["plan"]
        ask_user = state.get("ask_user")

        if not callable(ask_user):
            state["logs"].append("[CLARIFY] no CLI callback provided")
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
            state["logs"].append("[CLARIFY] existing project path clarified via CLI")
        else:
            state["logs"].append("[CLARIFY] no additional questions needed")
        return state

    @staticmethod
    def _prepare_execution_steps(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "prepare_execution_steps"
        project_dir = os.path.join(state["scope_root"], state["project_name"])
        os.makedirs(project_dir, exist_ok=True)
        state["touched_paths"].append(project_dir)
        state["logs"].append(f"[PREPARE] ensured project dir {project_dir}")
        return state

    @staticmethod
    def _execute_steps_in_sandbox(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_steps_in_sandbox"
        logs, touched_paths, errors = execute_dev_tasks(
            state.get("tasks", []),
            scope_root=state["scope_root"],
        )
        state["logs"].extend(logs)
        state["touched_paths"].extend(touched_paths)
        if errors:
            state["errors"].extend(errors)
            state["status"] = "failed"
        return state

    @staticmethod
    def _finalize_result(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "finalize_result"
        if state.get("status") != "failed":
            state["status"] = "completed"
        err_count = len(state.get("errors", []))
        state["final_summary"] = (
            f"Developer master finished with status={state['status']} and errors={err_count}."
        )
        state["logs"].append(f"[FINAL] {state['final_summary']}")
        return state
