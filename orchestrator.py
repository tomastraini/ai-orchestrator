# orchestrator.py

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict

from shared.state import PipelineState
from services.pm_service import create_plan, PMServiceError
from services.pm_context_store import PMContextStore
from services.dev_service import DevService
from services.pm.project_resolver import (
    is_vague_existing_project_request,
    resolve_project_candidates,
)


PROJECTS_ROOT = os.path.join(os.path.dirname(__file__), "projects")


def _print_plan(plan: Dict[str, Any]) -> None:
    print("\n===== PM PLAN (JSON) =====")
    print(json.dumps(plan, indent=2))
    print("==========================\n")


def _ask_approval() -> bool:
    ans = input("Approve plan? (y/N): ").strip().lower()
    return ans in ("y", "yes")


def _ask_clarification(question: str, round_index: int, max_rounds: int) -> str:
    print(f"\n[PM CLARIFICATION {round_index}/{max_rounds}] {question}")
    return input("Your answer: ").strip()


def _ask_dev_clarification(question: str) -> str:
    print(f"\n[DEV CLARIFICATION] {question}")
    return input("Your answer: ").strip()


def _confirm_existing_project(candidate: Dict[str, Any]) -> bool:
    name = str(candidate.get("name", ""))
    path_hint = str(candidate.get("path_hint", ""))
    score = candidate.get("score", 0.0)
    ans = input(
        f"Potential existing project match: {name} ({path_hint}, score={score}). Use it? (Y/n): "
    ).strip().lower()
    return ans in {"", "y", "yes"}


def run(requirement: str) -> int:
    request_id = str(uuid.uuid4())
    state = PipelineState(
        requirement=requirement,
        request_id=request_id,
        source_type="cli",
        source_id=request_id,
    )
    repo_root = os.path.dirname(__file__)
    context_store = PMContextStore(repo_root=repo_root)
    print(f"[REQUEST ID] {request_id}")
    preselected_project_ref: Dict[str, str] | None = None

    if is_vague_existing_project_request(requirement):
        candidates = resolve_project_candidates(requirement, PROJECTS_ROOT)
        if candidates:
            top = candidates[0]
            if _confirm_existing_project(top):
                preselected_project_ref = {
                    "name": str(top.get("name", "")),
                    "path_hint": str(top.get("path_hint", "")),
                }

    # 1) PM creates plan (reasoning lives only in PM service)
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
    except PMServiceError as e:
        print(f"[PM ERROR] {e}")
        return 1

    latest_context = context_store.load_context(request_id=state.request_id or "")
    dev_handoff = latest_context.get("dev_handoff") if isinstance(latest_context, dict) else None

    _print_plan(state.plan)

    # 2) Manual approval gate (deterministic)
    if not _ask_approval():
        print("Plan not approved. Exiting.")
        return 0

    # 3) Dev executes plan (engineering brain)
    dev = DevService(scope_root=PROJECTS_ROOT)
    result = dev.execute_plan(
        state.plan,
        request_id=state.request_id,
        ask_user=_ask_dev_clarification,
        handoff=dev_handoff if isinstance(dev_handoff, dict) else None,
    )

    state.branch_name = result.get("branch_name")
    state.build_logs = result.get("build_logs")
    state.dev_status = result.get("status") or "unknown"

    print(f"[DEV STATUS] {state.dev_status}")
    if state.build_logs:
        print("\n===== DEV LOGS =====")
        print(state.build_logs)
        print("====================\n")
    if state.branch_name:
        print(f"[BRANCH] {state.branch_name}")

    return 0


if __name__ == "__main__":
    req = input("Enter requirement: ").strip()
    if not req:
        print("Requirement cannot be empty.")
        raise SystemExit(1)
    raise SystemExit(run(req))
