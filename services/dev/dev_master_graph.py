from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from services.dev.dev_executor import execute_dev_tasks, execute_single_recovery_command
from shared.dev_schemas import DevTask, derive_project_name


DevAskFn = Callable[[str], str]
LLMCorrectorFn = Callable[[Dict[str, Any]], str]


class DevGraphState(TypedDict, total=False):
    request_id: str
    plan: Dict[str, Any]
    handoff: Dict[str, Any]
    scope_root: str
    project_name: str
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


class DevMasterGraph:
    def __init__(self) -> None:
        graph = StateGraph(DevGraphState)
        graph.add_node("ingest_pm_plan", self._ingest_pm_plan)
        graph.add_node("derive_dev_todos", self._derive_dev_todos)
        graph.add_node("ask_cli_clarifications_if_needed", self._ask_cli_clarifications_if_needed)
        graph.add_node("prepare_execution_steps", self._prepare_execution_steps)
        graph.add_node("execute_bootstrap_phase", self._execute_bootstrap_phase)
        graph.add_node("execute_implementation_phase", self._execute_implementation_phase)
        graph.add_node("finalize_result", self._finalize_result)

        graph.add_edge(START, "ingest_pm_plan")
        graph.add_edge("ingest_pm_plan", "derive_dev_todos")
        graph.add_edge("derive_dev_todos", "ask_cli_clarifications_if_needed")
        graph.add_edge("ask_cli_clarifications_if_needed", "prepare_execution_steps")
        graph.add_edge("prepare_execution_steps", "execute_bootstrap_phase")
        graph.add_edge("execute_bootstrap_phase", "execute_implementation_phase")
        graph.add_edge("execute_implementation_phase", "finalize_result")
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
        llm_corrector: LLMCorrectorFn | None = None,
        max_model_calls_per_run: int = 1,
    ) -> Dict[str, Any]:
        initial_state: DevGraphState = {
            "request_id": request_id,
            "plan": plan,
            "handoff": handoff or {},
            "scope_root": scope_root,
            "status": "running",
            "current_step": "init",
            "bootstrap_tasks": [],
            "implementation_targets": [],
            "validation_tasks": [],
            "clarifications": [],
            "logs": [],
            "touched_paths": [],
            "errors": [],
            "ask_user": ask_user,
            "llm_corrector": llm_corrector,
            "retry_count": 0,
            "max_retries": 5,
            "last_error": "",
            "attempt_history": [],
            "bootstrap_status": "pending",
            "implementation_status": "pending",
            "llm_calls_used": 0,
            "llm_call_budget": max(0, int(max_model_calls_per_run)),
        }
        result = self._compiled_graph.invoke(initial_state)
        result.pop("ask_user", None)
        result.pop("llm_corrector", None)
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
                }
            )

        state["bootstrap_tasks"] = bootstrap_tasks
        state["validation_tasks"] = validation_tasks
        state["implementation_targets"] = implementation_targets
        state["logs"].append(
            "[TODO] bootstrap_tasks="
            f"{len(bootstrap_tasks)} implementation_targets={len(implementation_targets)} "
            f"validation_tasks={len(validation_tasks)}"
        )
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

        # Ensure suggested folder layout exists before bootstrap commands run.
        handoff = state.get("handoff") or {}
        structure_plan = handoff.get("structure_plan")
        if isinstance(structure_plan, list):
            for entry in structure_plan:
                if not isinstance(entry, dict):
                    continue
                raw_path = str(entry.get("path", "")).strip()
                if not raw_path:
                    continue
                normalized = raw_path.replace("\\", "/")
                if normalized.startswith("projects/"):
                    rel = normalized.split("/", 1)[1]
                    target_dir = os.path.join(state["scope_root"], rel)
                else:
                    target_dir = os.path.join(state["scope_root"], normalized)
                os.makedirs(target_dir, exist_ok=True)
                state["touched_paths"].append(target_dir)
                state["logs"].append(f"[PREPARE] ensured structure dir {target_dir}")
        return state

    @staticmethod
    def _execute_bootstrap_phase(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_bootstrap_phase"
        state["logs"].append("[PHASE] bootstrap")
        logs, touched_paths, errors, attempt_history, pending_llm_task = execute_dev_tasks(
            state.get("bootstrap_tasks", []),
            scope_root=state["scope_root"],
            max_retries=int(state.get("max_retries", 5)),
            reserve_last_for_llm=True,
        )
        state["logs"].extend(logs)
        state["touched_paths"].extend(touched_paths)
        state["attempt_history"].extend(attempt_history)
        state["retry_count"] = len(state.get("attempt_history", []))
        if errors:
            state["errors"].extend(errors)
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = errors[-1]
            return state

        if pending_llm_task is None:
            state["bootstrap_status"] = "completed"
            return state

        llm_corrector = state.get("llm_corrector")
        if not callable(llm_corrector):
            state["errors"].append(
                f"{pending_llm_task['last_error']} (No LLM corrector available after deterministic retries.)"
            )
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = state["errors"][-1]
            return state

        if int(state.get("llm_calls_used", 0)) >= int(state.get("llm_call_budget", 0)):
            state["logs"].append(
                f"[LLM_BUDGET] reached ({state.get('llm_call_budget', 0)}); skipping correction."
            )
            state["errors"].append(
                f"{pending_llm_task['last_error']} (LLM model-call budget reached.)"
            )
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = state["errors"][-1]
            return state

        correction_input = {
            "task_id": pending_llm_task["task_id"],
            "task_kind": pending_llm_task["task_kind"],
            "cwd": pending_llm_task["cwd"],
            "command": pending_llm_task["last_command"],
            "error": pending_llm_task["last_error"],
            "last_attempt": pending_llm_task["last_attempt"],
            "scope_constraint": "All commands must remain within ./projects scope.",
            "push_constraint": "git push is blocked.",
        }
        try:
            state["llm_calls_used"] = int(state.get("llm_calls_used", 0)) + 1
            corrected_command = llm_corrector(correction_input).strip()
        except Exception as e:
            corrected_command = ""
            state["logs"].append(f"[LLM_REWRITE_ERROR] {pending_llm_task['task_id']}: {e}")

        if not corrected_command:
            state["errors"].append(
                f"{pending_llm_task['last_error']} (LLM did not provide corrected command.)"
            )
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = state["errors"][-1]
            return state

        state["logs"].append(f"[LLM_REWRITE] {pending_llm_task['task_id']} -> {corrected_command}")
        recover_logs, recover_error, recover_attempt = execute_single_recovery_command(
            task_id=str(pending_llm_task["task_id"]),
            task_kind=str(pending_llm_task["task_kind"]),
            scope_root=state["scope_root"],
            cwd=str(pending_llm_task["cwd"]),
            command=corrected_command,
        )
        recover_attempt["attempt"] = int(state.get("max_retries", 5))
        recover_attempt["strategy"] = "llm_rewrite"
        state["logs"].extend(recover_logs)
        state["attempt_history"].append(recover_attempt)
        state["retry_count"] = len(state.get("attempt_history", []))
        if recover_error:
            state["errors"].append(recover_error)
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = recover_error
            return state
        state["bootstrap_status"] = "completed"
        return state

    @staticmethod
    def _resolve_implementation_path(scope_root: str, expected_path_hint: str) -> str:
        path = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        while path.startswith("projects/"):
            path = path.split("/", 1)[1] if "/" in path else ""
        safe_path = os.path.abspath(os.path.join(scope_root, path))
        scope_abs = os.path.abspath(scope_root)
        if os.path.commonpath([scope_abs, safe_path]) != scope_abs:
            raise RuntimeError(f"Implementation target escapes scope: {expected_path_hint}")
        return safe_path

    @staticmethod
    def _execute_implementation_phase(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_implementation_phase"
        state["logs"].append("[PHASE] implementation")

        if state.get("bootstrap_status") == "failed":
            state["implementation_status"] = "impl_skipped"
            state["logs"].append("[IMPLEMENTATION] skipped due to bootstrap_failed")
            return state

        scope_root = state["scope_root"]
        targets = state.get("implementation_targets", [])
        try:
            for target in targets:
                expected = str(target.get("expected_path_hint", ""))
                modification_type = str(target.get("modification_type", "")).lower()
                details = str(target.get("details", "")).strip()
                safe_target = DevMasterGraph._resolve_implementation_path(scope_root, expected)

                if modification_type in {"create_directory", "mkdir", "create_dir"}:
                    os.makedirs(safe_target, exist_ok=True)
                    state["touched_paths"].append(safe_target)
                    state["logs"].append(f"[IMPLEMENTATION] ensured directory {safe_target}")
                    continue

                if os.path.splitext(safe_target)[1] == "":
                    os.makedirs(safe_target, exist_ok=True)
                    state["touched_paths"].append(safe_target)
                    state["logs"].append(f"[IMPLEMENTATION] ensured path {safe_target}")
                    continue

                os.makedirs(os.path.dirname(safe_target), exist_ok=True)
                if not os.path.exists(safe_target):
                    with open(safe_target, "w", encoding="utf-8") as fh:
                        fh.write(f"# TODO: {details or 'implementation placeholder'}\n")
                    state["logs"].append(f"[IMPLEMENTATION] created file {safe_target}")
                else:
                    with open(safe_target, "a", encoding="utf-8") as fh:
                        fh.write(f"\n# TODO: {details or 'implementation refinement'}\n")
                    state["logs"].append(f"[IMPLEMENTATION] updated file {safe_target}")
                state["touched_paths"].append(safe_target)
        except Exception as e:
            state["errors"].append(f"[IMPLEMENTATION_ERROR] {e}")
            state["implementation_status"] = "failed"
            state["status"] = "implementation_failed"
            return state

        state["implementation_status"] = "completed"
        return state

    @staticmethod
    def _finalize_result(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "finalize_result"
        if state.get("status") in {"bootstrap_failed", "implementation_failed"}:
            pass
        elif state.get("implementation_status") == "impl_skipped":
            state["status"] = "bootstrap_failed"
        else:
            state["status"] = "completed"
        err_count = len(state.get("errors", []))
        state["final_summary"] = (
            f"Developer master finished with status={state['status']} and errors={err_count}."
        )
        state["logs"].append(f"[FINAL] {state['final_summary']}")
        return state
