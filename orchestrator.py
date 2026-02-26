from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any, Dict

from services.dev.handoffpack_reader import load_handoff_with_fallback
from services.dev.dev_service import DevService
from services.pm.dev_handoff_store import DevHandoffStore
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


def _ask_resume_or_new() -> str:
    ans = input("Existing plan/handoff found. Resume it? (Y/n): ").strip().lower()
    return "resume" if ans in {"", "y", "yes"} else "new"


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


def _load_latest_plan_and_handoff(
    context_store: PMContextStore, repo_root: str
) -> tuple[Dict[str, Any] | None, Dict[str, Any] | None, str | None]:
    latest = context_store.get_latest_context()
    if not isinstance(latest, dict):
        return None, None, None
    plan = latest.get("final_plan") if isinstance(latest.get("final_plan"), dict) else None
    req_id = str(latest.get("request_id", "")).strip() or None
    handoff = latest.get("dev_handoff") if isinstance(latest.get("dev_handoff"), dict) else None
    if handoff is None:
        handoff_path = os.path.join(repo_root, ".orchestrator", "dev_handoff.json")
        handoff = load_handoff_with_fallback(handoff_path)
    return plan, handoff, req_id


def run(requirement: str, *, mode: str = "full", from_latest: bool = False) -> int:
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
    loaded_handoff: Dict[str, Any] | None = None
    loaded_request_id: str | None = None
    preselected_project_ref: Dict[str, str] | None = None

    print(f"[REQUEST ID] {request_id}")

    if from_latest:
        loaded_plan, loaded_handoff, loaded_request_id = _load_latest_plan_and_handoff(
            context_store, repo_root
        )
        if loaded_plan is None:
            print("[RESUME] no latest plan found.")
            return 1

    if mode in {"full", "plan"} and not from_latest:
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
                handoff_writer=DevHandoffStore(repo_root=repo_root),
            )
        except PMServiceError as e:
            print(f"[PM ERROR] {e}")
            return 1
        latest_context = context_store.load_context(request_id=state.request_id or "")
        loaded_handoff = latest_context.get("dev_handoff") if isinstance(latest_context, dict) else None
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

    if mode == "full" and not from_latest:
        latest_plan, latest_handoff, _ = _load_latest_plan_and_handoff(context_store, repo_root)
        if latest_plan and latest_handoff:
            decision = _ask_resume_or_new()
            if decision == "resume":
                state.plan = latest_plan
                loaded_handoff = latest_handoff

    print("[PHASE] approval_gate")
    if not _ask_approval():
        print("Plan not approved. Exiting.")
        return 0

    print("[PHASE] dev_execution")
    print("[DEV] execution starting immediately...")
    dev = DevService(scope_root=PROJECTS_ROOT)
    live_stream_enabled = True

    def _live_log_sink(line: str) -> None:
        print(line, flush=True)

    result = dev.execute_plan(
        state.plan,
        request_id=state.request_id,
        ask_user=_ask_dev_clarification,
        handoff=loaded_handoff if isinstance(loaded_handoff, dict) else None,
        log_sink=_live_log_sink if live_stream_enabled else None,
        handoff_writer=DevHandoffStore(repo_root=repo_root),
        run_artifacts_root=os.path.join(repo_root, ".orchestrator", "runs"),
    )

    state.branch_name = result.get("branch_name")
    state.build_logs = result.get("build_logs")
    state.dev_status = result.get("status") or "unknown"

    print(f"[DEV STATUS] {state.dev_status}")
    if state.build_logs:
        if live_stream_enabled:
            print("[DEV LOGS] already streamed live.")
        else:
            print("\n===== DEV LOGS =====")
            print(state.build_logs)
            print("====================\n")
    if state.branch_name:
        print(f"[BRANCH] {state.branch_name}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "plan", "execute"], default="full")
    parser.add_argument("--from-latest", action="store_true")
    parser.add_argument("--requirement", default="")
    args = parser.parse_args()

    req = (args.requirement or "").strip()
    if args.mode in {"full", "plan"} and not args.from_latest and not req:
        req = input("Enter requirement: ").strip()
    if args.mode in {"full", "plan"} and not args.from_latest and not req:
        print("Requirement cannot be empty.")
        raise SystemExit(1)
    if args.mode == "execute":
        raise SystemExit(run(req or "resume-execution", mode="execute", from_latest=True))
    raise SystemExit(run(req, mode=args.mode, from_latest=args.from_latest))
