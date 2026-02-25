from __future__ import annotations

import os
import platform
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from services.dev.dev_executor import execute_dev_tasks, execute_single_recovery_command
from services.workspace.project_index import detect_stack_from_markers
from shared.dev_schemas import DevTask, derive_project_name


DevAskFn = Callable[[str], str]
LLMCorrectorFn = Callable[[Dict[str, Any]], str]


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


class DevMasterGraph:
    def __init__(self) -> None:
        graph = StateGraph(DevGraphState)
        graph.add_node("ingest_pm_plan", self._ingest_pm_plan)
        graph.add_node("derive_dev_todos", self._derive_dev_todos)
        graph.add_node("dev_preflight_planning", self._dev_preflight_planning)
        graph.add_node("ask_cli_clarifications_if_needed", self._ask_cli_clarifications_if_needed)
        graph.add_node("prepare_execution_steps", self._prepare_execution_steps)
        graph.add_node("execute_bootstrap_phase", self._execute_bootstrap_phase)
        graph.add_node("execute_implementation_phase", self._execute_implementation_phase)
        graph.add_node("execute_validation_phase", self._execute_validation_phase)
        graph.add_node("finalize_result", self._finalize_result)

        graph.add_edge(START, "ingest_pm_plan")
        graph.add_edge("ingest_pm_plan", "derive_dev_todos")
        graph.add_edge("derive_dev_todos", "dev_preflight_planning")
        graph.add_edge("dev_preflight_planning", "ask_cli_clarifications_if_needed")
        graph.add_edge("ask_cli_clarifications_if_needed", "prepare_execution_steps")
        graph.add_edge("prepare_execution_steps", "execute_bootstrap_phase")
        graph.add_edge("execute_bootstrap_phase", "execute_implementation_phase")
        graph.add_edge("execute_implementation_phase", "execute_validation_phase")
        graph.add_edge("execute_validation_phase", "finalize_result")
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
        log_sink: Optional[Callable[[str], None]] = None,
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
            "validation_status": "pending",
            "active_project_root": "",
            "detected_stacks": [],
            "dev_preflight_plan": {},
            "llm_calls_used": 0,
            "llm_call_budget": max(0, int(max_model_calls_per_run)),
            "phase_status": {
                "ingest_pm_plan": "pending",
                "derive_dev_todos": "pending",
                "dev_preflight_planning": "pending",
                "ask_cli_clarifications_if_needed": "pending",
                "prepare_execution_steps": "pending",
                "execute_bootstrap_phase": "pending",
                "execute_implementation_phase": "pending",
                "execute_validation_phase": "pending",
                "finalize_result": "pending",
            },
            "implementation_pass_statuses": [],
            "log_sink": log_sink,
        }
        result = self._compiled_graph.invoke(initial_state)
        result.pop("ask_user", None)
        result.pop("llm_corrector", None)
        result.pop("log_sink", None)
        return result

    @staticmethod
    def _emit(state: DevGraphState, message: str) -> None:
        state["logs"].append(message)
        sink = state.get("log_sink")
        if callable(sink):
            try:
                sink(message)
            except Exception:
                pass

    @staticmethod
    def _ingest_pm_plan(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "ingest_pm_plan"
        DevMasterGraph._emit(state, "[PHASE_START] ingest_pm_plan")
        handoff = state.get("handoff") or {}
        project_root = handoff.get("project_root")
        if isinstance(project_root, str) and "/" in project_root:
            normalized_root = project_root.replace("\\", "/").strip().lstrip("./")
            state["project_root"] = normalized_root
            state["project_name"] = normalized_root.rstrip("/").split("/")[-1]
        else:
            project_name = derive_project_name(state["plan"])
            state["project_name"] = project_name
            state["project_root"] = f"projects/{project_name}"
        DevMasterGraph._emit(state, f"[INGEST] project='{state['project_name']}'")
        state["phase_status"]["ingest_pm_plan"] = "completed"
        return state

    @staticmethod
    def _derive_dev_todos(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "derive_dev_todos"
        DevMasterGraph._emit(state, "[PHASE_START] derive_dev_todos")
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
        DevMasterGraph._emit(
            state,
            "[TODO] bootstrap_tasks="
            f"{len(bootstrap_tasks)} implementation_targets={len(implementation_targets)} "
            f"validation_tasks={len(validation_tasks)}",
        )
        state["phase_status"]["derive_dev_todos"] = "completed"
        return state

    @staticmethod
    def _detect_stacks_for_root(project_dir: str) -> List[str]:
        markers: List[str] = []
        for marker in ["package.json", "pyproject.toml", "requirements.txt", "Gemfile", "Cargo.toml", "go.mod", "pom.xml"]:
            if os.path.exists(os.path.join(project_dir, marker)):
                markers.append(marker)
        top_entries = []
        try:
            top_entries = os.listdir(project_dir)
        except Exception:
            top_entries = []
        if any(x.endswith(".csproj") or x.endswith(".sln") for x in top_entries):
            markers.append("*.csproj")
        stacks = detect_stack_from_markers(markers, top_entries=top_entries)
        return stacks or ["generic"]

    @staticmethod
    def _default_validation_commands(stacks: List[str]) -> List[str]:
        if "dotnet" in stacks:
            return ["dotnet build", "dotnet test"]
        if "python" in stacks:
            return ["python -m pytest"]
        if "ruby" in stacks:
            return ["bundle exec rake test"]
        if "node" in stacks:
            return ["npm run build"]
        return []

    @staticmethod
    def _dev_preflight_planning(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "dev_preflight_planning"
        DevMasterGraph._emit(state, "[PHASE_START] dev_preflight_planning")
        project_root = str(state.get("project_root", f"projects/{state.get('project_name', 'project')}"))
        rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
        project_dir = os.path.join(state["scope_root"], rel)
        os.makedirs(project_dir, exist_ok=True)
        detected = DevMasterGraph._detect_stacks_for_root(project_dir)
        state["detected_stacks"] = detected
        state["active_project_root"] = project_dir

        validation_commands: List[str] = []
        for item in state.get("plan", {}).get("validation", []):
            if isinstance(item, str):
                val = item.strip()
                if any(val.startswith(prefix) for prefix in ["npm ", "pnpm ", "yarn ", "python ", "pytest", "dotnet ", "bundle ", "rake "]):
                    validation_commands.append(val)
        if not validation_commands:
            validation_commands = DevMasterGraph._default_validation_commands(detected)

        state["dev_preflight_plan"] = {
            "os": platform.system(),
            "active_project_root": project_dir,
            "detected_stacks": detected,
            "validation_commands": validation_commands,
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
        DevMasterGraph._emit(
            state,
            f"[PREFLIGHT] os={state['dev_preflight_plan']['os']} stacks={detected} active_root={project_dir}",
        )
        if validation_commands:
            DevMasterGraph._emit(state, f"[PREFLIGHT] validation_commands={validation_commands}")
        else:
            DevMasterGraph._emit(state, "[PREFLIGHT] no executable validation commands inferred")
        state["phase_status"]["dev_preflight_planning"] = "completed"
        return state

    @staticmethod
    def _ask_cli_clarifications_if_needed(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "ask_cli_clarifications_if_needed"
        DevMasterGraph._emit(state, "[PHASE_START] ask_cli_clarifications_if_needed")
        plan = state["plan"]
        ask_user = state.get("ask_user")

        if not callable(ask_user):
            DevMasterGraph._emit(state, "[CLARIFY] no CLI callback provided")
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
            DevMasterGraph._emit(state, "[CLARIFY] existing project path clarified via CLI")
        else:
            DevMasterGraph._emit(state, "[CLARIFY] no additional questions needed")
        state["phase_status"]["ask_cli_clarifications_if_needed"] = "completed"
        return state

    @staticmethod
    def _prepare_execution_steps(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "prepare_execution_steps"
        DevMasterGraph._emit(state, "[PHASE_START] prepare_execution_steps")
        project_root = str(state.get("project_root", f"projects/{state.get('project_name', 'project')}"))
        rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
        project_dir = os.path.join(state["scope_root"], rel)
        os.makedirs(project_dir, exist_ok=True)
        state["touched_paths"].append(project_dir)
        DevMasterGraph._emit(state, f"[PREPARE] ensured project dir {project_dir}")
        state["phase_status"]["prepare_execution_steps"] = "completed"
        return state

    @staticmethod
    def _execute_bootstrap_phase(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_bootstrap_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_bootstrap_phase")
        DevMasterGraph._emit(state, "[PHASE] bootstrap")
        logs, touched_paths, errors, attempt_history, pending_llm_task = execute_dev_tasks(
            state.get("bootstrap_tasks", []),
            scope_root=state["scope_root"],
            max_retries=int(state.get("max_retries", 5)),
            reserve_last_for_llm=True,
            log_sink=state.get("log_sink"),
            ask_confirmation=(lambda q: bool(state.get("ask_user")(q).strip().lower() in {"y", "yes", "true", "1"})) if callable(state.get("ask_user")) else None,
            stack_hint=(state.get("detected_stacks") or ["generic"])[0],
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
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state

        if pending_llm_task is None:
            # Resolve actual scaffold root from bootstrap output when tools create nested folders.
            state["active_project_root"] = DevMasterGraph._resolve_active_project_root_after_bootstrap(
                state=state,
                attempt_history=state.get("attempt_history", []),
            )
            DevMasterGraph._emit(state, f"[ROOT] active_project_root={state.get('active_project_root')}")
            state["bootstrap_status"] = "completed"
            DevMasterGraph._emit(
                state,
                f"[PHASE_SUMMARY] bootstrap attempts={len(attempt_history)} recovered=deterministic_or_clean"
            )
            state["phase_status"]["execute_bootstrap_phase"] = "completed"
            return state

        llm_corrector = state.get("llm_corrector")
        if not callable(llm_corrector):
            state["errors"].append(
                f"{pending_llm_task['last_error']} (No LLM corrector available after deterministic retries.)"
            )
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = state["errors"][-1]
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
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
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state

        correction_input = {
            "task_id": pending_llm_task["task_id"],
            "task_kind": pending_llm_task["task_kind"],
            "cwd": pending_llm_task["cwd"],
            "command": pending_llm_task["last_command"],
            "error": pending_llm_task["last_error"],
            "last_attempt": pending_llm_task["last_attempt"],
            "attempted_commands": pending_llm_task.get("attempted_commands", []),
            "scope_constraint": "All commands must remain within ./projects scope.",
            "push_constraint": "git push is blocked.",
        }
        try:
            state["llm_calls_used"] = int(state.get("llm_calls_used", 0)) + 1
            corrected_command = llm_corrector(correction_input).strip()
        except Exception as e:
            corrected_command = ""
            DevMasterGraph._emit(state, f"[LLM_REWRITE_ERROR] {pending_llm_task['task_id']}: {e}")

        if not corrected_command:
            state["errors"].append(
                f"{pending_llm_task['last_error']} (LLM did not provide corrected command.)"
            )
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = state["errors"][-1]
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state

        DevMasterGraph._emit(state, f"[LLM_REWRITE] {pending_llm_task['task_id']} -> {corrected_command}")
        DevMasterGraph._emit(
            state,
            f"[WHY_RETRY] deterministic retries exhausted for task={pending_llm_task['task_id']}, using llm_rewrite"
        )
        recover_logs, recover_error, recover_attempt = execute_single_recovery_command(
            task_id=str(pending_llm_task["task_id"]),
            task_kind=str(pending_llm_task["task_kind"]),
            scope_root=state["scope_root"],
            cwd=str(pending_llm_task["cwd"]),
            command=corrected_command,
            log_sink=state.get("log_sink"),
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
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state
        state["bootstrap_status"] = "completed"
        state["active_project_root"] = DevMasterGraph._resolve_active_project_root_after_bootstrap(
            state=state,
            attempt_history=state.get("attempt_history", []),
        )
        DevMasterGraph._emit(state, f"[ROOT] active_project_root={state.get('active_project_root')}")
        DevMasterGraph._emit(
            state,
            f"[PHASE_SUMMARY] bootstrap attempts={len(state.get('attempt_history', []))} recovered=llm"
        )
        state["phase_status"]["execute_bootstrap_phase"] = "completed"
        return state

    @staticmethod
    def _resolve_active_project_root_after_bootstrap(
        *,
        state: DevGraphState,
        attempt_history: List[Dict[str, Any]],
    ) -> str:
        active_root = str(state.get("active_project_root", "")).strip()
        scope_root = str(state.get("scope_root", "")).strip()
        if not scope_root:
            return active_root

        success_paths: List[str] = []
        for attempt in attempt_history:
            stdout = str(attempt.get("stdout", ""))
            m = re.search(r"Success!\s+Created\s+.+?\s+at\s+([^\r\n]+)", stdout)
            if m:
                success_paths.append(m.group(1).strip())
        for candidate in reversed(success_paths):
            cand_abs = os.path.abspath(candidate)
            if os.path.commonpath([os.path.abspath(scope_root), cand_abs]) == os.path.abspath(scope_root):
                return cand_abs
        return active_root

    @staticmethod
    def _resolve_implementation_path(scope_root: str, project_root: str, expected_path_hint: str) -> str:
        path = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        if path.startswith("projects/"):
            path = path.split("/", 1)[1] if "/" in path else ""
        elif project_root.startswith("projects/"):
            root_rel = project_root.split("/", 1)[1]
            path = f"{root_rel}/{path}".strip("/")
        safe_path = os.path.abspath(os.path.join(scope_root, path))
        scope_abs = os.path.abspath(scope_root)
        if os.path.commonpath([scope_abs, safe_path]) != scope_abs:
            raise RuntimeError(f"Implementation target escapes scope: {expected_path_hint}")
        return safe_path

    @staticmethod
    def _discover_existing_path(active_root: str, expected_path_hint: str, file_name: str) -> str:
        expected_tail = expected_path_hint.replace("\\", "/").strip().split("/", 2)
        suffix = ""
        if len(expected_tail) >= 3:
            suffix = expected_tail[2]
        targets = [file_name]
        if suffix:
            targets.append(suffix.split("/")[-1])
        for root, dirs, files in os.walk(active_root):
            dirs[:] = [d for d in dirs if d not in {"node_modules", ".git", ".venv", "__pycache__"}]
            for name in files:
                if name not in targets:
                    continue
                candidate = os.path.join(root, name)
                normalized = candidate.replace("\\", "/")
                if suffix and normalized.endswith(suffix):
                    return candidate
                if name == file_name:
                    return candidate
        return ""

    @staticmethod
    def _comment_for_path(path: str, text: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext in {".ts", ".tsx", ".js", ".jsx", ".java", ".c", ".cpp", ".go", ".rs"}:
            return f"// {text}\n"
        if ext in {".py", ".sh", ".rb", ".yml", ".yaml"}:
            return f"# {text}\n"
        return f"{text}\n"

    @staticmethod
    def _apply_target_in_pass(
        *,
        safe_target: str,
        file_name: str,
        active_root: str,
        modification_type: str,
        details: str,
        pass_index: int,
        expected_path_hint: str,
    ) -> Tuple[str, str, str]:
        if modification_type in {"create_directory", "mkdir", "create_dir"}:
            os.makedirs(safe_target, exist_ok=True)
            return "ensured_directory", f"pass={pass_index}", safe_target

        if os.path.splitext(safe_target)[1] == "":
            os.makedirs(safe_target, exist_ok=True)
            return "ensured_path", f"pass={pass_index}", safe_target

        os.makedirs(os.path.dirname(safe_target), exist_ok=True)
        update_like = modification_type in {"update", "replace", "modify", "patch"}
        if update_like and not os.path.exists(safe_target):
            discovered = DevMasterGraph._discover_existing_path(active_root, expected_path_hint, file_name)
            if discovered:
                safe_target = discovered
            else:
                return "missing_expected_file", "requires_discovery_or_clarification", safe_target
        if pass_index == 1:
            if not os.path.exists(safe_target):
                if update_like:
                    return "missing_expected_file", "not_created_due_to_update_policy", safe_target
                with open(safe_target, "w", encoding="utf-8") as fh:
                    fh.write(DevMasterGraph._comment_for_path(safe_target, f"IMPLEMENT: {details or 'initial implementation'}"))
                return "created_file", "initial_implementation", safe_target
            return "observed_file", "existing_file_preserved", safe_target

        if os.path.exists(safe_target) and os.path.getsize(safe_target) == 0:
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(DevMasterGraph._comment_for_path(safe_target, f"IMPLEMENT: {details or 'refinement implementation'}"))
            return "updated_file", "filled_empty_file", safe_target
        return "observed_file", "no_unnecessary_mutation", safe_target

    @staticmethod
    def _execute_implementation_phase(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_implementation_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_implementation_phase")
        DevMasterGraph._emit(state, "[PHASE] implementation")

        if state.get("bootstrap_status") == "failed":
            state["implementation_status"] = "impl_skipped"
            DevMasterGraph._emit(state, "[IMPLEMENTATION] skipped due to bootstrap_failed")
            state["phase_status"]["execute_implementation_phase"] = "skipped"
            return state

        scope_root = state["scope_root"]
        project_root = str(state.get("project_root", f"projects/{state.get('project_name', 'project')}"))
        active_root = str(state.get("active_project_root", "")).strip()
        if not active_root:
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            active_root = os.path.join(scope_root, rel)
        targets = state.get("implementation_targets", [])
        total_actions = 0
        try:
            for pass_index in (1, 2):
                pass_label = f"implementation_pass_{pass_index}"
                DevMasterGraph._emit(state, f"[PHASE] {pass_label}")
                DevMasterGraph._emit(
                    state,
                    f"[WHY_THIS_STEP] pass={pass_index} iterative target execution with context-aware mutation policy"
                )
                pass_actions = 0
                for target in targets:
                    expected = str(target.get("expected_path_hint", ""))
                    modification_type = str(target.get("modification_type", "")).lower()
                    details = str(target.get("details", "")).strip()
                    file_name = str(target.get("file_name", "")).strip() or os.path.basename(expected)
                    expected_norm = expected.replace("\\", "/").strip().lstrip("./")
                    if expected_norm.startswith("projects/") and active_root:
                        parts = expected_norm.split("/")
                        rel_tail = "/".join(parts[2:]) if len(parts) > 2 else ""
                        safe_target = os.path.join(active_root, rel_tail) if rel_tail else active_root
                    else:
                        safe_target = DevMasterGraph._resolve_implementation_path(scope_root, project_root, expected)
                    action, action_note, resolved_target = DevMasterGraph._apply_target_in_pass(
                        safe_target=safe_target,
                        file_name=file_name,
                        active_root=active_root,
                        modification_type=modification_type,
                        details=details,
                        pass_index=pass_index,
                        expected_path_hint=expected,
                    )
                    if action == "missing_expected_file":
                        raise RuntimeError(f"Expected target missing and discovery failed: {expected}")
                    state["touched_paths"].append(resolved_target)
                    DevMasterGraph._emit(
                        state,
                        f"[IMPLEMENTATION] pass={pass_index} action={action} target={resolved_target} note={action_note}"
                    )
                    pass_actions += 1
                    total_actions += 1
                state["implementation_pass_statuses"].append(f"{pass_label}:completed:{pass_actions}")
                DevMasterGraph._emit(
                    state,
                    f"[PASS_SUMMARY] {pass_label} actions={pass_actions} touched_total={len(state.get('touched_paths', []))}"
                )
        except Exception as e:
            state["errors"].append(f"[IMPLEMENTATION_ERROR] {e}")
            state["implementation_status"] = "failed"
            state["status"] = "implementation_failed"
            state["phase_status"]["execute_implementation_phase"] = "failed"
            return state

        state["implementation_status"] = "completed"
        state["phase_status"]["execute_implementation_phase"] = "completed"
        DevMasterGraph._emit(state, f"[IMPLEMENTATION_SUMMARY] total_actions={total_actions}")
        return state

    @staticmethod
    def _execute_validation_phase(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_validation_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_validation_phase")
        if state.get("bootstrap_status") == "failed" or state.get("implementation_status") == "failed":
            state["validation_status"] = "skipped"
            state["phase_status"]["execute_validation_phase"] = "skipped"
            DevMasterGraph._emit(state, "[VALIDATION] skipped due to previous failure")
            return state

        validation_tasks = state.get("validation_tasks", [])
        if not validation_tasks:
            state["validation_status"] = "completed"
            state["phase_status"]["execute_validation_phase"] = "completed"
            DevMasterGraph._emit(state, "[VALIDATION] no executable validations; marked completed")
            return state

        logs, touched_paths, errors, attempt_history, pending = execute_dev_tasks(
            validation_tasks,
            scope_root=state["scope_root"],
            max_retries=int(state.get("max_retries", 5)),
            reserve_last_for_llm=False,
            log_sink=state.get("log_sink"),
            stack_hint=(state.get("detected_stacks") or ["generic"])[0],
        )
        state["logs"].extend(logs)
        state["touched_paths"].extend(touched_paths)
        state["attempt_history"].extend(attempt_history)
        if pending:
            state["errors"].append(f"[VALIDATION] pending llm recovery unsupported for validation: {pending.get('task_id')}")
        if errors or pending:
            state["errors"].extend(errors)
            state["validation_status"] = "failed"
            state["status"] = "implementation_failed"
            state["phase_status"]["execute_validation_phase"] = "failed"
            DevMasterGraph._emit(state, "[VALIDATION] failed")
            return state
        state["validation_status"] = "completed"
        state["phase_status"]["execute_validation_phase"] = "completed"
        DevMasterGraph._emit(state, "[VALIDATION] completed")
        return state

    @staticmethod
    def _finalize_result(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "finalize_result"
        DevMasterGraph._emit(state, "[PHASE_START] finalize_result")
        if state.get("status") in {"bootstrap_failed", "implementation_failed"}:
            pass
        elif state.get("implementation_status") == "impl_skipped":
            state["status"] = "bootstrap_failed"
        elif state.get("validation_status") not in {"completed", "skipped"}:
            state["status"] = "implementation_failed"
        else:
            state["status"] = "completed"
        err_count = len(state.get("errors", []))
        state["final_summary"] = (
            f"Developer master finished with status={state['status']} and errors={err_count}. "
            f"phase_status={state.get('phase_status', {})} "
            f"pass_status={state.get('implementation_pass_statuses', [])}"
        )
        DevMasterGraph._emit(state, f"[FINAL] {state['final_summary']}")
        state["phase_status"]["finalize_result"] = "completed"
        return state
