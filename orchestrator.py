from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any, Dict

from services.execution.claude_cli_executor import ClaudeCodeCLIExecutor
from services.execution.openhands_runtime import OpenHandsRuntime
from services.pm.project_resolver import (
    is_vague_existing_project_request,
    resolve_project_candidates,
)
from services.pm.pm_context_store import PMContextStore
from services.pm.pm_service import PMServiceError, create_plan
from shared.state import PipelineState


PROJECTS_ROOT = os.path.join(os.path.dirname(__file__), "projects")


def _print_plan(plan: Dict[str, Any]) -> None:
    print("\n===== PM PLAN (JSON) =====")
    print(json.dumps(plan, indent=2))
    print("==========================\n")


def _ask_approval() -> bool:
    ans = input("Approve plan? (y/N): ").strip().lower()
    normalized = ans.strip("\"'`.,;:! ")
    return normalized in {"y", "yes", "true", "1", "ok", "approve"}


def _ask_clarification(question: str, round_index: int, max_rounds: int) -> str:
    print(f"\n[PM CLARIFICATION {round_index}/{max_rounds}] {question}")
    return input("Your answer: ").strip()


def _confirm_existing_project(candidate: Dict[str, Any]) -> bool:
    name = str(candidate.get("name", ""))
    path_hint = str(candidate.get("path_hint", ""))
    score = candidate.get("score", 0.0)
    ans = input(
        f"Potential existing project match: {name} ({path_hint}, score={score}). Use it? (Y/n): "
    ).strip().lower()
    return ans in {"", "y", "yes"}


def _load_latest_plan(context_store: PMContextStore) -> tuple[Dict[str, Any] | None, str | None]:
    latest = context_store.get_latest_context()
    if not isinstance(latest, dict):
        return None, None
    plan = latest.get("final_plan") if isinstance(latest.get("final_plan"), dict) else None
    req_id = str(latest.get("request_id", "")).strip() or None
    return plan, req_id


def _load_context_by_request_id(
    context_store: PMContextStore, request_id: str
) -> tuple[Dict[str, Any] | None, str | None]:
    entry = context_store.get_context_by_request_id(request_id)
    if not isinstance(entry, dict):
        return None, None
    plan = entry.get("final_plan") if isinstance(entry.get("final_plan"), dict) else None
    req_id = str(entry.get("request_id", "")).strip() or None
    return plan, req_id


def run(
    requirement: str,
    *,
    mode: str = "full",
    from_latest: bool = False,
    continue_from_request_id: str = "",
) -> int:
    request_id = str(uuid.uuid4())
    state = PipelineState(
        requirement=requirement,
        request_id=request_id,
        source_type="cli",
        source_id=request_id,
    )
    repo_root = os.path.dirname(__file__)
    context_store = PMContextStore(repo_root=repo_root)
    loaded_plan: Dict[str, Any] | None = None
    loaded_request_id: str | None = None
    preselected_project_ref: Dict[str, str] | None = None

    print(f"[REQUEST ID] {request_id}")

    if continue_from_request_id:
        loaded_plan, loaded_request_id = _load_context_by_request_id(
            context_store, continue_from_request_id
        )
        if loaded_plan is None:
            print(f"[RESUME] request not found: {continue_from_request_id}")
            return 1
    elif from_latest:
        loaded_plan, loaded_request_id = _load_latest_plan(context_store)
        if loaded_plan is None:
            print("[RESUME] no latest plan found.")
            return 1

    if mode in {"full", "plan"} and not from_latest and not continue_from_request_id:
        print("[PHASE] pm_planning")
        if is_vague_existing_project_request(requirement):
            candidates = resolve_project_candidates(requirement, PROJECTS_ROOT)
            if candidates:
                top = candidates[0]
                if _confirm_existing_project(top):
                    preselected_project_ref = {
                        "name": str(top.get("name", "")),
                        "path_hint": str(top.get("path_hint", "")),
                    }
        try:
            state.plan = create_plan(
                requirement=requirement,
                repo_name="ai-orchestrator",
                request_id=state.request_id,
                context_store=context_store,
                ask_user=_ask_clarification,
                max_rounds=3,
                preselected_project_ref=preselected_project_ref,
            )
        except PMServiceError as err:
            print(f"[PM ERROR] {err}")
            return 1
        _print_plan(state.plan)
    else:
        state.plan = loaded_plan
        state.request_id = loaded_request_id or state.request_id

    if state.plan is None:
        print("[ERROR] no plan available for requested mode.")
        return 1

    if mode == "plan":
        print("[DONE] plan mode complete.")
        return 0

    print("[PHASE] approval_gate")
    if not _ask_approval():
        print("Plan not approved. Exiting.")
        return 0

    print("[PHASE] execution")
    fallback_to_claude = (
        str(os.getenv("OPENHANDS_FALLBACK_TO_CLAUDE", "false")).strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if fallback_to_claude:
        print("[EXECUTION ENGINE] Claude fallback enabled")
        executor: Any = ClaudeCodeCLIExecutor(repo_root=repo_root)
    else:
        print("[EXECUTION ENGINE] OpenHands runtime")
        executor = OpenHandsRuntime(repo_root=repo_root)

    def _live_log_sink(line: str) -> None:
        print(line, flush=True)

    result = executor.execute_plan(
        state.plan,
        request_id=state.request_id or request_id,
        log_sink=_live_log_sink,
    )
    state.branch_name = result.get("branch_name")
    state.build_logs = result.get("build_logs")
    state.dev_status = str(result.get("status", "unknown"))

    print(f"[EXEC STATUS] {state.dev_status}")
    if state.build_logs:
        print("[EXEC LOGS] already streamed live.")
    if state.branch_name:
        print(f"[BRANCH] {state.branch_name}")
    return 0 if state.dev_status == "completed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "plan", "execute"], default="full")
    parser.add_argument("--from-latest", action="store_true")
    parser.add_argument("--continue-from-request-id", default="")
    parser.add_argument("--requirement", default="")
    args = parser.parse_args()

    req = (args.requirement or "").strip()
    if args.mode in {"full", "plan"} and not args.from_latest and not req:
        req = input("Enter requirement: ").strip()
    if args.mode in {"full", "plan"} and not args.from_latest and not req:
        print("Requirement cannot be empty.")
        raise SystemExit(1)
    if args.mode == "execute":
        raise SystemExit(
            run(
                req or "resume-execution",
                mode="execute",
                from_latest=True,
                continue_from_request_id=args.continue_from_request_id,
            )
        )
    raise SystemExit(
        run(
            req,
            mode=args.mode,
            from_latest=args.from_latest,
            continue_from_request_id=args.continue_from_request_id,
        )
    )

