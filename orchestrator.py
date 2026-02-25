# orchestrator.py

from __future__ import annotations

import json
import os
from typing import Any, Dict

from shared.state import PipelineState
from services.pm_service import create_plan, PMServiceError
from services.dev_service import DevService


REPO_PATH = os.path.join(os.path.dirname(__file__), "repos", "Clinigma-Transcripts")


def _print_plan(plan: Dict[str, Any]) -> None:
    print("\n===== PM PLAN (JSON) =====")
    print(json.dumps(plan, indent=2))
    print("==========================\n")


def _ask_approval() -> bool:
    ans = input("Approve plan? (y/N): ").strip().lower()
    return ans in ("y", "yes")


def run(requirement: str) -> int:
    state = PipelineState(requirement=requirement)

    # 1) PM creates plan (reasoning lives only in PM service)
    try:
        state.plan = create_plan(requirement=requirement)
    except PMServiceError as e:
        print(f"[PM ERROR] {e}")
        return 1

    _print_plan(state.plan)

    # 2) Manual approval gate (deterministic)
    if not _ask_approval():
        print("Plan not approved. Exiting.")
        return 0

    # 3) Dev executes plan (engineering brain)
    dev = DevService(repo_path=REPO_PATH)
    result = dev.execute_plan(state.plan)

    state.branch_name = result.get("branch_name")
    state.build_logs = result.get("build_logs")
    state.dev_status = result.get("status") or "unknown"

    print(f"[DEV STATUS] {state.dev_status}")
    if state.branch_name:
        print(f"[BRANCH] {state.branch_name}")

    return 0


if __name__ == "__main__":
    req = input("Enter requirement: ").strip()
    if not req:
        print("Requirement cannot be empty.")
        raise SystemExit(1)
    raise SystemExit(run(req))
