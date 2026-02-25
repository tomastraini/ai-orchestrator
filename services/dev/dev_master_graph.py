from __future__ import annotations

import hashlib
import os
import platform
import re
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from services.dev.dev_executor import execute_dev_tasks, execute_single_recovery_command
from services.workspace.project_index import detect_stack_from_markers
from shared.dev_schemas import DevChecklistItem, DevTask, derive_project_name


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
    root_resolution_evidence: Dict[str, Any]
    llm_context_contract: Dict[str, Any]
    internal_checklist: List[Dict[str, Any]]
    checklist_index: Dict[str, int]
    final_compile_tasks: List[DevTask]
    final_compile_status: str
    task_outcomes: List[Dict[str, Any]]
    checklist_cursor: str


class DevMasterGraph:
    VALIDATION_COMMAND_PREFIXES = (
        "npm ",
        "pnpm ",
        "yarn ",
        "python ",
        "pytest",
        "dotnet ",
        "bundle ",
        "rake ",
        "make ",
        "./",
        "bash ",
        "sh ",
    )
    PROJECT_MARKER_FILES = (
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Pipfile",
        "Gemfile",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "composer.json",
    )
    SOURCE_DIR_HINTS = ("src", "app", "lib")

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
        graph.add_node("execute_final_compile_gate", self._execute_final_compile_gate)
        graph.add_node("finalize_result", self._finalize_result)

        graph.add_edge(START, "ingest_pm_plan")
        graph.add_edge("ingest_pm_plan", "derive_dev_todos")
        graph.add_edge("derive_dev_todos", "dev_preflight_planning")
        graph.add_edge("dev_preflight_planning", "ask_cli_clarifications_if_needed")
        graph.add_edge("ask_cli_clarifications_if_needed", "prepare_execution_steps")
        graph.add_edge("prepare_execution_steps", "execute_bootstrap_phase")
        graph.add_edge("execute_bootstrap_phase", "execute_implementation_phase")
        graph.add_edge("execute_implementation_phase", "execute_validation_phase")
        graph.add_edge("execute_validation_phase", "execute_final_compile_gate")
        graph.add_edge("execute_final_compile_gate", "finalize_result")
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
            "final_compile_status": "pending",
            "active_project_root": "",
            "detected_stacks": [],
            "dev_preflight_plan": {},
            "final_compile_tasks": [],
            "internal_checklist": [],
            "checklist_index": {},
            "task_outcomes": [],
            "checklist_cursor": "",
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
                "execute_final_compile_gate": "pending",
                "finalize_result": "pending",
            },
            "implementation_pass_statuses": [],
            "log_sink": log_sink,
            "root_resolution_evidence": {},
            "llm_context_contract": {},
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
    def _reindex_checklist(state: DevGraphState) -> None:
        checklist = state.get("internal_checklist", [])
        state["checklist_index"] = {
            str(item.get("id", "")): idx
            for idx, item in enumerate(checklist)
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }

    @staticmethod
    def _upsert_checklist_item(state: DevGraphState, item: DevChecklistItem) -> None:
        checklist = state.get("internal_checklist", [])
        index = state.get("checklist_index", {})
        payload = asdict(item)
        item_id = item.id
        if item_id in index:
            checklist[index[item_id]] = payload
        else:
            checklist.append(payload)
        state["internal_checklist"] = checklist
        DevMasterGraph._reindex_checklist(state)

    @staticmethod
    def _find_checklist_item(state: DevGraphState, item_id: str) -> Optional[Dict[str, Any]]:
        idx = state.get("checklist_index", {}).get(item_id)
        if idx is None:
            return None
        checklist = state.get("internal_checklist", [])
        if idx < 0 or idx >= len(checklist):
            return None
        item = checklist[idx]
        return item if isinstance(item, dict) else None

    @staticmethod
    def _append_item_evidence(item: Dict[str, Any], evidence: Optional[Dict[str, Any]]) -> None:
        if not evidence:
            return
        current = item.get("evidence")
        if not isinstance(current, list):
            current = []
        current.append(evidence)
        item["evidence"] = current

    @staticmethod
    def _set_checklist_status(
        state: DevGraphState,
        item_id: str,
        status: str,
        *,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        item = DevMasterGraph._find_checklist_item(state, item_id)
        if not item:
            return
        item["status"] = status
        DevMasterGraph._append_item_evidence(item, evidence)
        state["checklist_cursor"] = item_id
        DevMasterGraph._emit(
            state,
            f"[CHECKLIST] item={item_id} status={status}",
        )

    @staticmethod
    def _next_actionable_checklist_item(state: DevGraphState) -> Optional[Dict[str, Any]]:
        checklist = state.get("internal_checklist", [])
        completed = {
            str(item.get("id", ""))
            for item in checklist
            if isinstance(item, dict) and str(item.get("status", "")) == "completed"
        }
        for item in checklist:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "pending"))
            if status in {"completed", "failed"}:
                continue
            deps = item.get("depends_on", [])
            if isinstance(deps, list) and any(str(dep) not in completed for dep in deps):
                continue
            return item
        return None

    @staticmethod
    def _all_mandatory_checklist_items_completed(state: DevGraphState) -> bool:
        for item in state.get("internal_checklist", []):
            if not isinstance(item, dict):
                continue
            if not bool(item.get("mandatory", True)):
                continue
            if str(item.get("status", "")) != "completed":
                return False
        return True

    @staticmethod
    def _build_internal_checklist(state: DevGraphState) -> None:
        handoff = state.get("handoff") or {}
        restored = handoff.get("internal_checklist")
        restored_by_id: Dict[str, Dict[str, Any]] = {}
        if isinstance(restored, list) and restored:
            for item in restored:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id", "")).strip()
                if not item_id:
                    continue
                restored_by_id[item_id] = item

        checklist: List[Dict[str, Any]] = []
        for task in state.get("bootstrap_tasks", []):
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="bootstrap",
                    description=task.description,
                    task_ref=task.id,
                    success_criteria=["command exits with code 0"],
                )
            )
            checklist.append(restored_by_id.get(item_id, default_item))
        for idx, target in enumerate(state.get("implementation_targets", []), start=1):
            file_name = str(target.get("file_name", "")).strip() or f"target_{idx}"
            item_id = f"todo_impl_{idx}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="implementation",
                    description=f"implement {file_name}",
                    target_ref=str(target.get("expected_path_hint", file_name)),
                    success_criteria=["target file mutated with evidence"],
                )
            )
            checklist.append(restored_by_id.get(item_id, default_item))
        for task in state.get("validation_tasks", []):
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="validation",
                    description=task.description,
                    task_ref=task.id,
                    success_criteria=["validation task completed"],
                    mandatory=False,
                )
            )
            checklist.append(restored_by_id.get(item_id, default_item))
        for task in state.get("final_compile_tasks", []):
            deps = [str(item.get("id")) for item in checklist if isinstance(item, dict)]
            item_id = f"todo_{task.id}"
            default_item = asdict(
                DevChecklistItem(
                    id=item_id,
                    kind="final_compile",
                    description=task.description,
                    task_ref=task.id,
                    depends_on=deps,
                    success_criteria=["compile/build gate command completed"],
                )
            )
            checklist.append(restored_by_id.get(item_id, default_item))
        state["internal_checklist"] = checklist
        DevMasterGraph._reindex_checklist(state)
        if restored_by_id:
            DevMasterGraph._emit(
                state,
                f"[CHECKLIST] restored_and_reconciled items={len(checklist)} restored={len(restored_by_id)}",
            )
        else:
            DevMasterGraph._emit(state, f"[CHECKLIST] initialized items={len(checklist)}")

    @staticmethod
    def _ingest_pm_plan(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "ingest_pm_plan"
        DevMasterGraph._emit(state, "[PHASE_START] ingest_pm_plan")
        handoff = state.get("handoff") or {}
        if isinstance(handoff.get("task_outcomes"), list):
            state["task_outcomes"] = [x for x in handoff.get("task_outcomes", []) if isinstance(x, dict)]
        if isinstance(handoff.get("checklist_cursor"), str):
            state["checklist_cursor"] = str(handoff.get("checklist_cursor", ""))
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
    def _is_long_running_validation_command(command: str) -> bool:
        low = f" {str(command or '').lower()} "
        hints = [
            " npm run dev ",
            " npm start ",
            " pnpm dev ",
            " yarn dev ",
            " vite ",
            " next dev ",
            " flask run ",
            " uvicorn ",
            " rails server ",
            " dotnet watch ",
        ]
        return any(token in low for token in hints)

    @staticmethod
    def _infer_final_compile_commands(
        *,
        project_dir: str,
        stacks: List[str],
        validation_commands: List[str],
    ) -> List[str]:
        compile_candidates: List[str] = []
        for command in validation_commands:
            if not DevMasterGraph._is_long_running_validation_command(command):
                compile_candidates.append(command)
        if compile_candidates:
            return compile_candidates

        default_candidates = DevMasterGraph._default_validation_commands(stacks)
        for command in default_candidates:
            if not DevMasterGraph._is_long_running_validation_command(command):
                compile_candidates.append(command)

        package_json = os.path.join(project_dir, "package.json")
        if not compile_candidates and os.path.exists(package_json):
            compile_candidates.append("npm run build")

        return compile_candidates

    @staticmethod
    def _extract_validation_command(raw: str) -> str:
        val = (raw or "").strip()
        if not val:
            return ""
        if any(val.startswith(prefix) for prefix in DevMasterGraph.VALIDATION_COMMAND_PREFIXES):
            return val
        # Accept backticked shell snippets from PM text, e.g. "Run `npm run build`".
        backticked = re.findall(r"`([^`]+)`", val)
        for token in backticked:
            normalized = token.strip()
            if any(normalized.startswith(prefix) for prefix in DevMasterGraph.VALIDATION_COMMAND_PREFIXES):
                return normalized
        return ""

    @staticmethod
    def _dev_preflight_planning(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "dev_preflight_planning"
        DevMasterGraph._emit(state, "[PHASE_START] dev_preflight_planning")
        project_root = str(state.get("project_root", f"projects/{state.get('project_name', 'project')}"))
        rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
        project_dir = os.path.join(state["scope_root"], rel)
        os.makedirs(project_dir, exist_ok=True)
        detected = DevMasterGraph._detect_stacks_for_root(project_dir)
        if detected == ["generic"]:
            stack_text_tokens: List[str] = []
            for task in state.get("bootstrap_tasks", []):
                if isinstance(task, DevTask):
                    stack_text_tokens.append(str(task.command or ""))
            plan_stack = state.get("plan", {}).get("stack", {})
            if isinstance(plan_stack, dict):
                stack_text_tokens.append(str(plan_stack.get("frontend", "")))
                stack_text_tokens.append(str(plan_stack.get("backend", "")))
            stack_text = " ".join(stack_text_tokens).lower()
            if any(tok in stack_text for tok in ["npm", "pnpm", "yarn", "vite", "react", "next"]):
                detected = ["node"]
            elif any(tok in stack_text for tok in ["dotnet", "c#", "asp.net"]):
                detected = ["dotnet"]
            elif any(tok in stack_text for tok in ["python", "pip", "pytest", "django", "flask", "fastapi"]):
                detected = ["python"]
            elif any(tok in stack_text for tok in ["ruby", "rails", "bundler", "rake"]):
                detected = ["ruby"]
        state["detected_stacks"] = detected
        state["active_project_root"] = project_dir

        raw_validation_requirements = [
            str(x).strip()
            for x in state.get("plan", {}).get("validation", [])
            if isinstance(x, str) and str(x).strip()
        ]
        validation_commands: List[str] = []
        unresolved_validation_requirements: List[str] = []
        for requirement in raw_validation_requirements:
            cmd = DevMasterGraph._extract_validation_command(requirement)
            if cmd:
                validation_commands.append(cmd)
            else:
                unresolved_validation_requirements.append(requirement)

        if not validation_commands and not raw_validation_requirements:
            validation_commands = DevMasterGraph._default_validation_commands(detected)
        final_compile_commands = DevMasterGraph._infer_final_compile_commands(
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
        DevMasterGraph._build_internal_checklist(state)
        DevMasterGraph._emit(
            state,
            f"[PREFLIGHT] os={state['dev_preflight_plan']['os']} stacks={detected} active_root={project_dir}",
        )
        if validation_commands:
            DevMasterGraph._emit(state, f"[PREFLIGHT] validation_commands={validation_commands}")
        else:
            DevMasterGraph._emit(state, "[PREFLIGHT] no executable validation commands inferred")
        if unresolved_validation_requirements:
            DevMasterGraph._emit(
                state,
                f"[PREFLIGHT] unresolved_validation_requirements={unresolved_validation_requirements}",
            )
        if final_compile_commands:
            DevMasterGraph._emit(state, f"[PREFLIGHT] final_compile_commands={final_compile_commands}")
        else:
            DevMasterGraph._emit(state, "[PREFLIGHT] no terminating compile commands inferred")
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
    def _is_within_scope(scope_root: str, candidate_path: str) -> bool:
        try:
            scope_abs = os.path.abspath(scope_root)
            cand_abs = os.path.abspath(candidate_path)
            return os.path.commonpath([scope_abs, cand_abs]) == scope_abs
        except Exception:
            return False

    @staticmethod
    def _has_project_marker(path: str) -> bool:
        if not os.path.isdir(path):
            return False
        try:
            names = set(os.listdir(path))
        except Exception:
            return False
        if any(marker in names for marker in DevMasterGraph.PROJECT_MARKER_FILES):
            return True
        return any(name.endswith(".csproj") or name.endswith(".sln") for name in names)

    @staticmethod
    def _source_hint_count(path: str) -> int:
        if not os.path.isdir(path):
            return 0
        count = 0
        for name in DevMasterGraph.SOURCE_DIR_HINTS:
            if os.path.isdir(os.path.join(path, name)):
                count += 1
        return count

    @staticmethod
    def _normalize_target_tail(expected_path_hint: str, project_name: str) -> str:
        normalized = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        if normalized.startswith("projects/"):
            parts = [p for p in normalized.split("/") if p]
            if len(parts) >= 3:
                if project_name and parts[1] != project_name:
                    return "/".join(parts[2:])
                return "/".join(parts[2:])
            return ""
        return normalized

    @staticmethod
    def _resolve_active_project_root_after_bootstrap(
        *,
        state: DevGraphState,
        attempt_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        scope_root = str(state.get("scope_root", "")).strip()
        project_root = str(state.get("project_root", "")).strip()
        project_name = str(state.get("project_name", "")).strip()
        active_root = str(state.get("active_project_root", "")).strip()
        if not scope_root:
            return {"selected_root": active_root, "confidence": 0, "candidates": [], "ambiguous": False}

        scope_abs = os.path.abspath(scope_root)
        expected_tails = [
            DevMasterGraph._normalize_target_tail(str(t.get("expected_path_hint", "")), project_name)
            for t in state.get("implementation_targets", [])
            if isinstance(t, dict)
        ]
        expected_tails = [x for x in expected_tails if x]

        score_map: Dict[str, int] = {}
        reasons_map: Dict[str, List[str]] = {}

        def _add_candidate(path: str, score: int, reason: str) -> None:
            if not path:
                return
            cand_abs = os.path.abspath(path)
            if not DevMasterGraph._is_within_scope(scope_abs, cand_abs):
                return
            if not os.path.isdir(cand_abs):
                return
            score_map[cand_abs] = score_map.get(cand_abs, 0) + int(score)
            reasons_map.setdefault(cand_abs, []).append(reason)

        # Seed candidates from known roots/cwds.
        if active_root:
            _add_candidate(active_root, 25, "initial_active_root")
        if project_root:
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            _add_candidate(os.path.join(scope_abs, rel), 15, "project_root_hint")

        # Parse stdout/stderr for path evidence.
        regexes = [
            r"Success!\s+Created\s+.+?\s+at\s+([^\r\n]+)",
            r"Scaffolding project in\s+([^\r\n]+)",
            r"Created project at\s+([^\r\n]+)",
            r"Project created at\s+([^\r\n]+)",
        ]
        for attempt in attempt_history:
            blob = f"{attempt.get('stdout', '')}\n{attempt.get('stderr', '')}"
            for pattern in regexes:
                for m in re.finditer(pattern, blob, flags=re.IGNORECASE):
                    candidate = m.group(1).strip().rstrip(".")
                    _add_candidate(candidate, 90, f"stdout_pattern:{pattern}")
            attempt_cwd = str(attempt.get("cwd", "")).strip()
            if attempt_cwd:
                _add_candidate(attempt_cwd, 12, "attempt_cwd")

        for touched in state.get("touched_paths", []):
            if isinstance(touched, str):
                _add_candidate(touched, 10, "touched_path")

        # Scan immediate and nested directories for marker evidence.
        search_roots: Set[str] = set()
        for base in list(score_map.keys()):
            search_roots.add(base)
            parent = os.path.dirname(base)
            if parent and DevMasterGraph._is_within_scope(scope_abs, parent):
                search_roots.add(parent)

        ignore_dirs = {"node_modules", ".git", ".venv", "__pycache__", "dist", "build", ".next", ".cache"}
        for search_root in list(search_roots):
            if not os.path.isdir(search_root):
                continue
            for root, dirs, _files in os.walk(search_root):
                dirs[:] = [d for d in dirs if d not in ignore_dirs]
                depth = os.path.relpath(root, search_root).count(os.sep)
                if depth > 3:
                    dirs[:] = []
                    continue
                if DevMasterGraph._has_project_marker(root):
                    _add_candidate(root, max(40, 70 - (depth * 10)), f"marker_depth:{depth}")
                hints = DevMasterGraph._source_hint_count(root)
                if hints:
                    _add_candidate(root, hints * 6, f"source_hints:{hints}")

        for candidate in list(score_map.keys()):
            base = os.path.basename(candidate.rstrip("/\\"))
            if project_name and base == project_name:
                _add_candidate(candidate, 8, "basename_matches_project")
            if expected_tails:
                matches = 0
                for tail in expected_tails:
                    if os.path.exists(os.path.join(candidate, tail)):
                        matches += 1
                if matches:
                    _add_candidate(candidate, matches * 20, f"target_tail_matches:{matches}")

        ranked = sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)
        candidates = [
            {"path": path, "score": score, "reasons": reasons_map.get(path, [])}
            for path, score in ranked
        ]
        if not candidates:
            return {
                "selected_root": active_root,
                "confidence": 0,
                "candidates": [],
                "ambiguous": False,
            }

        top = candidates[0]
        second_score = candidates[1]["score"] if len(candidates) > 1 else -999
        confidence = int(top["score"])
        ambiguous = len(candidates) > 1 and confidence < 60 and (top["score"] - second_score) < 10
        return {
            "selected_root": top["path"],
            "confidence": confidence,
            "candidates": candidates[:5],
            "ambiguous": ambiguous,
        }

    @staticmethod
    def _build_llm_context_contract(state: DevGraphState) -> Dict[str, Any]:
        scope_root = str(state.get("scope_root", "")).strip()
        resolved_root = str(state.get("active_project_root", "")).strip()
        project_name = str(state.get("project_name", "")).strip()
        root_evidence = state.get("root_resolution_evidence", {}) if isinstance(state.get("root_resolution_evidence"), dict) else {}
        normalized_targets: List[Dict[str, str]] = []
        for target in state.get("implementation_targets", []):
            if not isinstance(target, dict):
                continue
            expected = str(target.get("expected_path_hint", ""))
            file_name = str(target.get("file_name", ""))
            resolved = ""
            try:
                resolved = DevMasterGraph._resolve_target_file_path(
                    scope_root=scope_root,
                    project_root=str(state.get("project_root", "")),
                    active_project_root=resolved_root,
                    expected_path_hint=expected,
                    file_name=file_name,
                )
            except Exception:
                resolved = ""
            normalized_targets.append(
                {
                    "expected_path_hint": expected,
                    "file_name": file_name,
                    "resolved_absolute_path": resolved,
                }
            )

        tree_snapshot: List[str] = []
        if resolved_root and os.path.isdir(resolved_root):
            try:
                entries = sorted(os.listdir(resolved_root))[:30]
                tree_snapshot = entries
            except Exception:
                tree_snapshot = []

        return {
            "scope_root": scope_root,
            "resolved_active_root": resolved_root,
            "project_name": project_name,
            "candidate_roots": root_evidence.get("candidates", []),
            "root_confidence": root_evidence.get("confidence", 0),
            "path_aliases": {
                "project_root": str(state.get("project_root", "")),
                "active_project_root": resolved_root,
            },
            "normalized_targets": normalized_targets,
            "active_root_tree_snapshot": tree_snapshot,
        }

    @staticmethod
    def _file_sha1(path: str) -> str:
        if not os.path.exists(path) or not os.path.isfile(path):
            return ""
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()

    @staticmethod
    def _execute_bootstrap_phase(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_bootstrap_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_bootstrap_phase")
        DevMasterGraph._emit(state, "[PHASE] bootstrap")
        next_item = DevMasterGraph._next_actionable_checklist_item(state)
        if next_item:
            DevMasterGraph._emit(
                state,
                f"[CHECKLIST] next_actionable={next_item.get('id')} kind={next_item.get('kind')}",
            )
        plan_constraints = [
            str(x).strip()
            for x in state.get("plan", {}).get("constraints", [])
            if isinstance(x, str) and str(x).strip()
        ]
        bootstrap_tasks = []
        for task in state.get("bootstrap_tasks", []):
            item = DevMasterGraph._find_checklist_item(state, f"todo_{task.id}")
            if item and str(item.get("status", "")) == "completed":
                continue
            bootstrap_tasks.append(task)
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{task.id}",
                "in_progress",
                evidence={"phase": "bootstrap", "task_id": task.id},
            )
        if not bootstrap_tasks:
            state["bootstrap_status"] = "completed"
            state["phase_status"]["execute_bootstrap_phase"] = "completed"
            DevMasterGraph._emit(state, "[BOOTSTRAP] no pending bootstrap checklist items")
            return state

        logs, touched_paths, errors, attempt_history, pending_llm_task, outcomes = execute_dev_tasks(
            bootstrap_tasks,
            scope_root=state["scope_root"],
            max_retries=int(state.get("max_retries", 5)),
            reserve_last_for_llm=True,
            log_sink=state.get("log_sink"),
            ask_confirmation=(lambda q: bool(state.get("ask_user")(q).strip().lower() in {"y", "yes", "true", "1"})) if callable(state.get("ask_user")) else None,
            ask_runtime_prompt=(lambda q: state.get("ask_user")(f"[DEV RUNTIME PROMPT] {q} (y/N)")) if callable(state.get("ask_user")) else None,
            stack_hint=(state.get("detected_stacks") or ["generic"])[0],
            interactive_prompt_timeout_seconds=60.0,
            constraints=plan_constraints,
            command_run_mode="terminating",
        )
        state["logs"].extend(logs)
        state["touched_paths"].extend(touched_paths)
        state["attempt_history"].extend(attempt_history)
        state["task_outcomes"].extend(outcomes)
        state["retry_count"] = len(state.get("attempt_history", []))
        for outcome in outcomes:
            checklist_id = f"todo_{outcome.get('task_id', '')}"
            status = "completed" if outcome.get("status") == "completed" else "blocked"
            DevMasterGraph._set_checklist_status(state, checklist_id, status, evidence=outcome)
        if errors:
            state["errors"].extend(errors)
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = errors[-1]
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state

        if pending_llm_task is None:
            root_evidence = DevMasterGraph._resolve_active_project_root_after_bootstrap(
                state=state,
                attempt_history=state.get("attempt_history", []),
            )
            state["root_resolution_evidence"] = root_evidence
            selected_root = str(root_evidence.get("selected_root", "")).strip()
            confidence = int(root_evidence.get("confidence", 0))
            ambiguous = bool(root_evidence.get("ambiguous", False))
            candidates = root_evidence.get("candidates", [])
            existing_root = str(state.get("active_project_root", "")).strip()
            trusted_existing = bool(existing_root and os.path.abspath(existing_root) == os.path.abspath(selected_root))
            DevMasterGraph._emit(state, f"[ROOT_EVIDENCE] confidence={confidence} candidates={candidates}")
            if (confidence < 45 or ambiguous) and not trusted_existing and len(candidates) >= 2 and callable(state.get("ask_user")):
                c1 = str(candidates[0].get("path", ""))
                c2 = str(candidates[1].get("path", ""))
                question = (
                    "Detected multiple possible project roots. Choose 1 or 2:\n"
                    f"1) {c1}\n"
                    f"2) {c2}"
                )
                answer = str(state.get("ask_user")(question)).strip().lower()
                if answer in {"1", "a"}:
                    selected_root = c1
                elif answer in {"2", "b"}:
                    selected_root = c2
                elif c1.lower() in answer:
                    selected_root = c1
                elif c2.lower() in answer:
                    selected_root = c2
                else:
                    state["errors"].append("[ROOT] unresolved root ambiguity from CLI answer.")
                    state["status"] = "bootstrap_failed"
                    state["bootstrap_status"] = "failed"
                    state["last_error"] = state["errors"][-1]
                    state["phase_status"]["execute_bootstrap_phase"] = "failed"
                    return state
            elif confidence < 45 and not trusted_existing and not callable(state.get("ask_user")):
                state["errors"].append("[ROOT] low confidence active root resolution without CLI clarification.")
                state["status"] = "bootstrap_failed"
                state["bootstrap_status"] = "failed"
                state["last_error"] = state["errors"][-1]
                state["phase_status"]["execute_bootstrap_phase"] = "failed"
                return state

            state["active_project_root"] = selected_root
            state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
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
            "execution_context": state.get("llm_context_contract", {}),
            "root_resolution_evidence": state.get("root_resolution_evidence", {}),
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
            ask_runtime_prompt=(lambda q: state.get("ask_user")(f"[DEV RUNTIME PROMPT] {q} (y/N)")) if callable(state.get("ask_user")) else None,
            interactive_prompt_timeout_seconds=60.0,
        )
        recover_attempt["attempt"] = int(state.get("max_retries", 5))
        recover_attempt["strategy"] = "llm_rewrite"
        state["logs"].extend(recover_logs)
        state["attempt_history"].append(recover_attempt)
        state["retry_count"] = len(state.get("attempt_history", []))
        if recover_error:
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{pending_llm_task['task_id']}",
                "failed",
                evidence={"phase": "bootstrap", "error": recover_error},
            )
            state["errors"].append(recover_error)
            state["status"] = "bootstrap_failed"
            state["bootstrap_status"] = "failed"
            state["last_error"] = recover_error
            state["phase_status"]["execute_bootstrap_phase"] = "failed"
            return state
        DevMasterGraph._set_checklist_status(
            state,
            f"todo_{pending_llm_task['task_id']}",
            "completed",
            evidence=recover_attempt,
        )
        state["bootstrap_status"] = "completed"
        root_evidence = DevMasterGraph._resolve_active_project_root_after_bootstrap(
            state=state,
            attempt_history=state.get("attempt_history", []),
        )
        state["root_resolution_evidence"] = root_evidence
        state["active_project_root"] = str(root_evidence.get("selected_root", state.get("active_project_root", ""))).strip()
        state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
        DevMasterGraph._emit(
            state,
            f"[ROOT_EVIDENCE] confidence={root_evidence.get('confidence', 0)} candidates={root_evidence.get('candidates', [])}",
        )
        DevMasterGraph._emit(state, f"[ROOT] active_project_root={state.get('active_project_root')}")
        DevMasterGraph._emit(
            state,
            f"[PHASE_SUMMARY] bootstrap attempts={len(state.get('attempt_history', []))} recovered=llm"
        )
        state["phase_status"]["execute_bootstrap_phase"] = "completed"
        return state

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
    def _resolve_target_file_path(
        *,
        scope_root: str,
        project_root: str,
        active_project_root: str,
        expected_path_hint: str,
        file_name: str,
    ) -> str:
        scope_abs = os.path.abspath(os.path.normpath(scope_root))
        expected_norm = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        project_root_norm = (project_root or "").replace("\\", "/").strip().lstrip("./")
        file_name_norm = (file_name or "").strip()

        project_name = ""
        if project_root_norm.startswith("projects/"):
            parts = [p for p in project_root_norm.split("/") if p]
            if len(parts) >= 2:
                project_name = parts[1]

        if active_project_root:
            active_abs = os.path.abspath(os.path.normpath(active_project_root))
            if os.path.commonpath([scope_abs, active_abs]) == scope_abs:
                base_root = active_abs
            else:
                raise RuntimeError(f"Active project root escapes scope: {active_project_root}")
        else:
            rel = project_root_norm.split("/", 1)[1] if project_root_norm.startswith("projects/") else project_root_norm
            base_root = os.path.abspath(os.path.join(scope_abs, rel))

        rel_path = expected_norm
        if expected_norm.startswith("projects/"):
            parts = [p for p in expected_norm.split("/") if p]
            if len(parts) >= 3:
                # If PM project name drifted, still anchor to active root.
                if project_name and parts[1] != project_name:
                    rel_path = "/".join(parts[2:])
                else:
                    rel_path = "/".join(parts[2:])
            elif len(parts) == 2:
                rel_path = file_name_norm or parts[-1]
            else:
                rel_path = file_name_norm
        elif not rel_path:
            rel_path = file_name_norm

        if not rel_path and file_name_norm:
            rel_path = file_name_norm

        safe_path = os.path.abspath(os.path.join(base_root, rel_path))
        if os.path.commonpath([scope_abs, safe_path]) != scope_abs:
            raise RuntimeError(f"Implementation target escapes scope: {expected_path_hint}")
        return safe_path

    @staticmethod
    def _discover_existing_path(active_root: str, expected_path_hint: str, file_name: str) -> str:
        expected_norm = expected_path_hint.replace("\\", "/").strip().lstrip("./")
        targets = [name for name in {file_name.strip(), os.path.basename(expected_norm)} if name]
        expected_suffix = ""
        if expected_norm.startswith("projects/"):
            parts = [p for p in expected_norm.split("/") if p]
            if len(parts) >= 3:
                expected_suffix = "/".join(parts[2:])
        else:
            expected_suffix = expected_norm

        best_candidate = ""
        best_score = -1
        for root, dirs, files in os.walk(active_root):
            dirs[:] = [
                d for d in dirs if d not in {"node_modules", ".git", ".venv", "__pycache__", "dist", "build", ".next"}
            ]
            for name in files:
                if name not in targets:
                    continue
                candidate = os.path.join(root, name)
                normalized = candidate.replace("\\", "/")
                if expected_suffix and normalized.endswith(expected_suffix):
                    return candidate
                score = 0
                if expected_suffix:
                    exp_dirs = expected_suffix.split("/")[:-1]
                    rel = os.path.relpath(candidate, active_root).replace("\\", "/")
                    rel_dirs = rel.split("/")[:-1]
                    for i, part in enumerate(exp_dirs):
                        if i < len(rel_dirs) and rel_dirs[i] == part:
                            score += 1
                else:
                    score = 100 - len(os.path.relpath(candidate, active_root).split(os.sep))
                if score > best_score:
                    best_candidate = candidate
                    best_score = score
        return best_candidate

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

        is_directory_target = modification_type in {"create_directory", "mkdir", "create_dir"}
        if not is_directory_target and os.path.splitext(file_name)[1]:
            is_directory_target = False
        elif os.path.splitext(safe_target)[1] == "":
            # Keep legacy behavior only for explicit directory targets or extensionless names.
            is_directory_target = True

        if is_directory_target:
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
        if not os.path.exists(safe_target):
            if update_like:
                return "missing_expected_file", "not_created_due_to_update_policy", safe_target
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(DevMasterGraph._comment_for_path(safe_target, f"IMPLEMENT: {details or 'initial implementation'}"))
            return "created_file", "initial_implementation", safe_target

        with open(safe_target, "r", encoding="utf-8", errors="ignore") as fh:
            original = fh.read()
        marker = DevMasterGraph._comment_for_path(
            safe_target,
            f"IMPLEMENT PASS {pass_index}: {details or 'target mutation'}",
        )
        if marker not in original:
            if pass_index == 1 and update_like:
                new_content = marker + original
            else:
                new_content = original + ("" if original.endswith("\n") else "\n") + marker
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(new_content)
            return "updated_file", "mutated_with_marker", safe_target

        if os.path.getsize(safe_target) == 0:
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(marker)
            return "updated_file", "filled_empty_file", safe_target
        return "observed_file", "marker_already_present", safe_target

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
        state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
        DevMasterGraph._emit(state, f"[CONTEXT] active_root={active_root}")
        targets = state.get("implementation_targets", [])
        pending_targets: List[Tuple[int, Dict[str, str]]] = []
        target_proofs: Dict[str, Dict[str, Any]] = {}
        for idx, target in enumerate(targets, start=1):
            item = DevMasterGraph._find_checklist_item(state, f"todo_impl_{idx}")
            if item and str(item.get("status", "")) == "completed":
                continue
            pending_targets.append((idx, target))
            checklist_id = f"todo_impl_{idx}"
            DevMasterGraph._set_checklist_status(
                state,
                checklist_id,
                "in_progress",
                evidence={"phase": "implementation", "pass": 0},
            )
        if not pending_targets:
            state["implementation_status"] = "completed"
            state["phase_status"]["execute_implementation_phase"] = "completed"
            DevMasterGraph._emit(state, "[IMPLEMENTATION] no pending implementation checklist items")
            return state
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
                for idx, target in pending_targets:
                    expected = str(target.get("expected_path_hint", ""))
                    modification_type = str(target.get("modification_type", "")).lower()
                    details = str(target.get("details", "")).strip()
                    file_name = str(target.get("file_name", "")).strip() or os.path.basename(expected)
                    safe_target = DevMasterGraph._resolve_target_file_path(
                        scope_root=scope_root,
                        project_root=project_root,
                        active_project_root=active_root,
                        expected_path_hint=expected,
                        file_name=file_name,
                    )
                    key = str(expected or file_name or safe_target)
                    if key not in target_proofs:
                        target_proofs[key] = {"before_hash": DevMasterGraph._file_sha1(safe_target), "after_hash": ""}
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
                    target_proofs[key]["after_hash"] = DevMasterGraph._file_sha1(resolved_target)
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
            for idx, target in pending_targets:
                key = str(target.get("expected_path_hint", "") or target.get("file_name", ""))
                proof = target_proofs.get(key, {})
                before_hash = str(proof.get("before_hash", ""))
                after_hash = str(proof.get("after_hash", ""))
                if before_hash == after_hash:
                    raise RuntimeError(
                        f"Target mutation proof missing for {key or f'implementation_target_{idx}'}: no content delta detected."
                    )
                DevMasterGraph._set_checklist_status(
                    state,
                    f"todo_impl_{idx}",
                    "completed",
                    evidence={
                        "phase": "implementation",
                        "before_hash": before_hash,
                        "after_hash": after_hash,
                    },
                )
        except Exception as e:
            state["errors"].append(f"[IMPLEMENTATION_ERROR] {e}")
            for idx, _target in pending_targets:
                DevMasterGraph._set_checklist_status(
                    state,
                    f"todo_impl_{idx}",
                    "failed",
                    evidence={"phase": "implementation", "error": str(e)},
                )
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

        preflight = state.get("dev_preflight_plan", {}) if isinstance(state.get("dev_preflight_plan"), dict) else {}
        raw_requirements = preflight.get("raw_validation_requirements", [])
        unresolved_requirements = preflight.get("unresolved_validation_requirements", [])
        validation_tasks = state.get("validation_tasks", [])

        if raw_requirements and unresolved_requirements and not validation_tasks:
            msg = (
                "[VALIDATION] required validations were provided by PM but none were executable: "
                f"{unresolved_requirements}"
            )
            for item in state.get("internal_checklist", []):
                if isinstance(item, dict) and str(item.get("kind")) == "validation":
                    item["status"] = "failed"
                    DevMasterGraph._append_item_evidence(item, {"phase": "validation", "error": msg})
            state["errors"].append(msg)
            state["validation_status"] = "failed"
            state["status"] = "implementation_failed"
            state["phase_status"]["execute_validation_phase"] = "failed"
            DevMasterGraph._emit(state, msg)
            return state

        filtered_validation_tasks: List[DevTask] = []
        for task in validation_tasks:
            item = DevMasterGraph._find_checklist_item(state, f"todo_{task.id}")
            if item and str(item.get("status", "")) == "completed":
                continue
            filtered_validation_tasks.append(task)

        if not filtered_validation_tasks:
            state["validation_status"] = "completed"
            state["phase_status"]["execute_validation_phase"] = "completed"
            DevMasterGraph._emit(state, "[VALIDATION] no pending executable validations; marked completed")
            return state

        active_root = str(state.get("active_project_root", "")).strip()
        if active_root:
            filtered_validation_tasks = [
                DevTask(
                    id=task.id,
                    description=task.description,
                    command=task.command,
                    cwd=active_root,
                    kind=task.kind,
                )
                for task in filtered_validation_tasks
            ]
            DevMasterGraph._emit(state, f"[VALIDATION] reconciled task cwd to active root {active_root}")
        state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
        for task in filtered_validation_tasks:
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{task.id}",
                "in_progress",
                evidence={"phase": "validation", "task_id": task.id},
            )

        logs, touched_paths, errors, attempt_history, pending, outcomes = execute_dev_tasks(
            filtered_validation_tasks,
            scope_root=state["scope_root"],
            max_retries=int(state.get("max_retries", 5)),
            reserve_last_for_llm=False,
            log_sink=state.get("log_sink"),
            stack_hint=(state.get("detected_stacks") or ["generic"])[0],
            ask_runtime_prompt=(lambda q: state.get("ask_user")(f"[DEV RUNTIME PROMPT] {q} (y/N)")) if callable(state.get("ask_user")) else None,
            interactive_prompt_timeout_seconds=60.0,
            constraints=[
                str(x).strip()
                for x in state.get("plan", {}).get("constraints", [])
                if isinstance(x, str) and str(x).strip()
            ],
            command_run_mode="auto",
        )
        state["logs"].extend(logs)
        state["touched_paths"].extend(touched_paths)
        state["attempt_history"].extend(attempt_history)
        state["task_outcomes"].extend(outcomes)
        for outcome in outcomes:
            checklist_id = f"todo_{outcome.get('task_id', '')}"
            status = "completed" if outcome.get("status") == "completed" else "failed"
            DevMasterGraph._set_checklist_status(state, checklist_id, status, evidence=outcome)
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
    def _execute_final_compile_gate(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_final_compile_gate"
        DevMasterGraph._emit(state, "[PHASE_START] execute_final_compile_gate")
        if (
            state.get("bootstrap_status") == "failed"
            or state.get("implementation_status") == "failed"
            or state.get("validation_status") == "failed"
        ):
            state["final_compile_status"] = "skipped"
            state["phase_status"]["execute_final_compile_gate"] = "skipped"
            DevMasterGraph._emit(state, "[FINAL_COMPILE] skipped due to previous failure")
            return state

        compile_tasks = state.get("final_compile_tasks", [])
        had_compile_tasks = bool(compile_tasks)
        filtered_compile_tasks: List[DevTask] = []
        for task in compile_tasks:
            item = DevMasterGraph._find_checklist_item(state, f"todo_{task.id}")
            if item and str(item.get("status", "")) == "completed":
                continue
            filtered_compile_tasks.append(task)
        compile_tasks = filtered_compile_tasks
        if had_compile_tasks and not compile_tasks:
            state["final_compile_status"] = "completed"
            state["phase_status"]["execute_final_compile_gate"] = "completed"
            DevMasterGraph._emit(state, "[FINAL_COMPILE] all compile checklist items already completed")
            return state
        if not compile_tasks:
            state["errors"].append("[FINAL_COMPILE] no terminating compile/build command inferred.")
            state["final_compile_status"] = "failed"
            state["status"] = "implementation_failed"
            state["phase_status"]["execute_final_compile_gate"] = "failed"
            return state

        active_root = str(state.get("active_project_root", "")).strip()
        if active_root:
            compile_tasks = [
                DevTask(
                    id=task.id,
                    description=task.description,
                    command=task.command,
                    cwd=active_root,
                    kind=task.kind,
                )
                for task in compile_tasks
            ]

        for task in compile_tasks:
            DevMasterGraph._set_checklist_status(
                state,
                f"todo_{task.id}",
                "in_progress",
                evidence={"phase": "final_compile", "task_id": task.id},
            )

        logs, touched_paths, errors, attempt_history, pending, outcomes = execute_dev_tasks(
            compile_tasks,
            scope_root=state["scope_root"],
            max_retries=int(state.get("max_retries", 5)),
            reserve_last_for_llm=False,
            log_sink=state.get("log_sink"),
            stack_hint=(state.get("detected_stacks") or ["generic"])[0],
            ask_runtime_prompt=(lambda q: state.get("ask_user")(f"[DEV RUNTIME PROMPT] {q} (y/N)")) if callable(state.get("ask_user")) else None,
            interactive_prompt_timeout_seconds=60.0,
            constraints=[
                str(x).strip()
                for x in state.get("plan", {}).get("constraints", [])
                if isinstance(x, str) and str(x).strip()
            ],
            command_run_mode="terminating",
        )
        state["logs"].extend(logs)
        state["touched_paths"].extend(touched_paths)
        state["attempt_history"].extend(attempt_history)
        state["task_outcomes"].extend(outcomes)
        for outcome in outcomes:
            checklist_id = f"todo_{outcome.get('task_id', '')}"
            status = "completed" if outcome.get("status") == "completed" else "failed"
            DevMasterGraph._set_checklist_status(state, checklist_id, status, evidence=outcome)
        if pending:
            errors.append(f"[FINAL_COMPILE] pending llm recovery unsupported for final compile: {pending.get('task_id')}")
        if errors:
            state["errors"].extend(errors)
            state["final_compile_status"] = "failed"
            state["status"] = "implementation_failed"
            state["phase_status"]["execute_final_compile_gate"] = "failed"
            DevMasterGraph._emit(state, "[FINAL_COMPILE] failed")
            return state

        state["final_compile_status"] = "completed"
        state["phase_status"]["execute_final_compile_gate"] = "completed"
        DevMasterGraph._emit(state, "[FINAL_COMPILE] completed")
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
        elif state.get("final_compile_status") != "completed":
            state["status"] = "implementation_failed"
        elif not DevMasterGraph._all_mandatory_checklist_items_completed(state):
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
        DevMasterGraph._emit(state, f"[FINAL] {state['final_summary']}")
        state["phase_status"]["finalize_result"] = "completed"
        return state
