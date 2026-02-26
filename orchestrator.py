from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any, Dict

from services.dev.handoffpack_reader import load_handoff_with_fallback
from services.dev.dev_service import DevService
from services.dev.dev_session_store import DevSessionStore
from services.pm.dev_handoff_store import DevHandoffStore
from services.pm.project_resolver import (
    is_vague_existing_project_request,
    resolve_project_candidates,
)
from services.pm.pm_context_store import PMContextStore
from services.pm.pm_service import PMServiceError, create_plan
from shared.state import PipelineState


PROJECTS_ROOT = os.path.join(os.path.dirname(__file__), "projects")
CONTINUATION_FLAG = "DEV_CONTINUATION_LOOP_ENABLED"


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


def _ask_followup() -> bool:
    ans = input("Do you want improvements based on what was done? (Y/n): ").strip().lower()
    return ans in {"", "y", "yes"}


def _ask_delta_requirement(
    prompt: str = "Describe the improvement/follow-up requirement (or type 'done' to end): ",
) -> str:
    return input(prompt).strip()


def _is_end_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    explicit = {
        "exit",
        "end",
        "stop",
        "done",
        "quit",
        "no more",
        "that's all",
        "that is all",
        "finish",
        "finished",
    }
    if normalized in explicit:
        return True
    endings = (
        "no more improvements",
        "no further improvements",
        "we are done",
        "i am done",
    )
    return any(ending in normalized for ending in endings)


def _format_followup_options(guidance: Dict[str, Any]) -> str:
    options = guidance.get("followup_options", [])
    if not isinstance(options, list):
        return ""
    formatted: list[str] = []
    for idx, option in enumerate(options, start=1):
        if not isinstance(option, dict):
            continue
        label = str(option.get("label", "")).strip()
        description = str(option.get("description", "")).strip()
        if not label:
            continue
        formatted.append(f"{idx}. {label}" + (f" - {description}" if description else ""))
    return "\n".join(formatted).strip()


def _collect_followup_requirement(*, status: str, guidance: Dict[str, Any]) -> tuple[str, str, str]:
    normalized_status = str(status or "").strip().lower()
    if normalized_status == "completed" and not _ask_followup():
        return ("close", "", "completed_user_confirmed_no_more_improvements")

    needs_validation_clarification = bool(guidance.get("needs_validation_clarification", False))
    options_text = _format_followup_options(guidance)
    prompt = "Describe the next improvement requirement (or type 'done' to end): "
    if needs_validation_clarification:
        prompt = "Validation method is needed. Describe how to validate next (or type 'done' to end): "
        if options_text:
            print("[VALIDATION FOLLOW-UP OPTIONS]")
            print(options_text)

    while True:
        delta = _ask_delta_requirement(prompt)
        if _is_end_intent(delta):
            return ("close", "", "explicit_end_intent")
        if delta:
            reason = "validation_clarification_provided" if needs_validation_clarification else "delta_requirement_provided"
            return ("continue", delta, reason)
        if normalized_status == "completed":
            return ("close", "", "completed_no_additional_improvements")
        print("[CONTINUATION] follow-up requirement is required for non-terminal status. Type 'done' to end.")


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "y", "yes", "true", "on"}


def _append_jsonl(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


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


def _load_context_by_request_id(
    context_store: PMContextStore, repo_root: str, request_id: str
) -> tuple[Dict[str, Any] | None, Dict[str, Any] | None, str | None]:
    entry = context_store.get_context_by_request_id(request_id)
    if not isinstance(entry, dict):
        return None, None, None
    plan = entry.get("final_plan") if isinstance(entry.get("final_plan"), dict) else None
    handoff = entry.get("dev_handoff") if isinstance(entry.get("dev_handoff"), dict) else None
    req_id = str(entry.get("request_id", "")).strip() or None
    if handoff is None:
        handoff = load_handoff_with_fallback(os.path.join(repo_root, ".orchestrator", "dev_handoff.json"))
    return plan, handoff, req_id


def _current_iteration_index(session: Dict[str, Any]) -> int:
    chain = session.get("run_chain", [])
    if not isinstance(chain, list) or not chain:
        return 0
    return max(int(x.get("iteration_index", 0) or 0) for x in chain if isinstance(x, dict))


def run(
    requirement: str,
    *,
    mode: str = "full",
    from_latest: bool = False,
    continuation_mode: str = "always",
    session_id: str = "",
    continue_from_request_id: str = "",
    delta_requirement: str = "",
    close_session: bool = False,
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
    session_store = DevSessionStore(repo_root=repo_root)
    continuation_enabled = continuation_mode != "off" and _env_flag(CONTINUATION_FLAG, default=True)
    if continuation_mode != "off" and not continuation_enabled:
        print(f"[CONTINUATION] disabled by env flag {CONTINUATION_FLAG}=false")
    if continuation_mode == "off":
        print("[CONTINUATION] explicitly deactivated by continuation_mode=off")
    loaded_plan: Dict[str, Any] | None = None
    loaded_handoff: Dict[str, Any] | None = None
    loaded_request_id: str | None = None
    preselected_project_ref: Dict[str, str] | None = None
    active_session_id = str(session_id or "").strip()
    initial_delta = str(delta_requirement or "").strip()

    print(f"[REQUEST ID] {request_id}")
    if close_session and active_session_id:
        session_store.close_session(active_session_id, reason="closed_by_cli")
        print(f"[SESSION] closed {active_session_id}")
        return 0

    if continue_from_request_id:
        loaded_plan, loaded_handoff, loaded_request_id = _load_context_by_request_id(
            context_store, repo_root, continue_from_request_id
        )
        if loaded_plan is None:
            print(f"[RESUME] request not found: {continue_from_request_id}")
            return 1
    elif from_latest:
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
            continuation_ctx: Dict[str, Any] | None = None
            if continuation_enabled and initial_delta and isinstance(loaded_handoff, dict):
                continuation_ctx = {
                    "session_id": active_session_id,
                    "parent_request_id": str(loaded_handoff.get("request_id", "")).strip(),
                    "iteration_index": 1,
                    "delta_requirement": initial_delta,
                    "prior_run_summary": "",
                    "trigger_type": "improvement",
                    "continuation_mode": continuation_mode,
                    "continuation_reason": "initial_delta",
                    "carry_forward_memory": True,
                    "immutable_constraints": loaded_handoff.get("constraints", []),
                    "continuation_guidance": (
                        dict(loaded_handoff.get("continuation", {}).get("continuation_guidance", {}))
                        if isinstance(loaded_handoff.get("continuation"), dict)
                        and isinstance(loaded_handoff.get("continuation", {}).get("continuation_guidance"), dict)
                        else {}
                    ),
                }
            state.plan = create_plan(
                requirement=initial_delta or requirement,
                repo_name="ai-orchestrator",
                request_id=state.request_id,
                context_store=context_store,
                ask_user=_ask_clarification,
                max_rounds=3,
                preselected_project_ref=preselected_project_ref,
                handoff_writer=DevHandoffStore(repo_root=repo_root),
                continuation_context=continuation_ctx,
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

    if continuation_enabled and not active_session_id:
        latest = session_store.get_latest_session()
        if isinstance(latest, dict) and str(latest.get("status", "active")) == "active":
            active_session_id = str(latest.get("session_id", "")).strip()
        if not active_session_id:
            created = session_store.create_session(root_requirement=requirement or "followup")
            active_session_id = str(created.get("session_id", "")).strip()
            print(f"[SESSION] created {active_session_id}")

    while True:
        effective_handoff = dict(loaded_handoff) if isinstance(loaded_handoff, dict) else {}
        continuation = effective_handoff.get("continuation", {})
        if not isinstance(continuation, dict):
            continuation = {}
        current_session = session_store.get_session(active_session_id) if active_session_id else None
        parent_request_id = str(loaded_request_id or continuation.get("parent_request_id", "")).strip()
        iteration_index = (_current_iteration_index(current_session or {}) + 1) if continuation_enabled else 0
        continuation.update(
            {
                "session_id": active_session_id if continuation_enabled else "",
                "parent_request_id": parent_request_id,
                "iteration_index": iteration_index,
                "delta_requirement": initial_delta,
                "prior_run_summary": str(continuation.get("prior_run_summary", "")).strip(),
                "carry_forward_memory": True,
                "trigger_type": "improvement" if initial_delta else ("retry" if from_latest else "initial"),
                "continuation_mode": continuation_mode,
                "continuation_reason": "delta_followup" if initial_delta else "initial",
            }
        )
        effective_handoff["continuation"] = continuation
        result = dev.execute_plan(
            state.plan,
            request_id=state.request_id,
            ask_user=_ask_dev_clarification,
            handoff=effective_handoff,
            log_sink=_live_log_sink if live_stream_enabled else None,
            handoff_writer=DevHandoffStore(repo_root=repo_root),
            run_artifacts_root=os.path.join(repo_root, ".orchestrator", "runs"),
            session_store=session_store if continuation_enabled else None,
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

        if continuation_enabled:
            summary = str(result.get("final_summary", "")).strip()
            if summary:
                print(f"[DEV SUMMARY] {summary}")
            artifacts_root = os.path.join(repo_root, ".orchestrator")
            _append_jsonl(
                os.path.join(artifacts_root, "iteration_summaries.jsonl"),
                {
                    "session_id": active_session_id,
                    "request_id": state.request_id,
                    "iteration_index": iteration_index,
                    "status": state.dev_status,
                    "summary": summary,
                },
            )
            _append_jsonl(
                os.path.join(artifacts_root, "continuation_decisions.jsonl"),
                {
                    "event": "continuation_offered",
                    "session_id": active_session_id,
                    "request_id": state.request_id,
                    "decision": "offered",
                    "continuation_eligible": bool(result.get("continuation_eligible", False)),
                    "loop_enforced_default": bool(continuation_mode != "off"),
                },
            )
            if not bool(result.get("continuation_eligible", False)):
                if state.dev_status == "implementation_failed":
                    print("[CONTINUATION] blocked: terminal failure gate approved.")
                _append_jsonl(
                    os.path.join(artifacts_root, "continuation_decisions.jsonl"),
                    {
                        "event": "continuation_completed",
                        "session_id": active_session_id,
                        "request_id": state.request_id,
                        "final_status": state.dev_status,
                        "hard_stop_triggered": state.dev_status == "implementation_failed",
                    },
                )
                session_store.close_session(active_session_id, reason="hard_stop_or_blocked")
                _write_json(
                    os.path.join(artifacts_root, "session_summary.json"),
                    {
                        "session_id": active_session_id,
                        "status": "blocked",
                        "last_request_id": state.request_id,
                        "last_status": state.dev_status,
                    },
                )
                break
            guidance = result.get("continuation_guidance", {})
            if not isinstance(guidance, dict):
                guidance = {}
            action, next_delta, decision_reason = _collect_followup_requirement(
                status=state.dev_status,
                guidance=guidance,
            )
            _append_jsonl(
                os.path.join(artifacts_root, "continuation_decisions.jsonl"),
                {
                    "event": "continuation_accepted" if action == "continue" else "continuation_declined",
                    "session_id": active_session_id,
                    "request_id": state.request_id,
                    "decision": "accepted" if action == "continue" else "declined",
                    "reason": decision_reason,
                },
            )
            if action != "continue":
                session_store.close_session(active_session_id, reason=decision_reason or "declined_followup")
                _append_jsonl(
                    os.path.join(artifacts_root, "continuation_decisions.jsonl"),
                    {
                        "event": "session_closed",
                        "session_id": active_session_id,
                        "request_id": state.request_id,
                        "reason": decision_reason or "declined_followup",
                    },
                )
                _write_json(
                    os.path.join(artifacts_root, "session_summary.json"),
                    {
                        "session_id": active_session_id,
                        "status": "closed",
                        "last_request_id": state.request_id,
                        "last_status": state.dev_status,
                    },
                )
                print(f"[SESSION] closed {active_session_id}")
                break
            initial_delta = next_delta.strip()
            _append_jsonl(
                os.path.join(artifacts_root, "requirement_deltas.jsonl"),
                {
                    "event": "continuation_started",
                    "session_id": active_session_id,
                    "parent_request_id": state.request_id,
                    "delta_requirement": initial_delta,
                    "reason": decision_reason,
                },
            )
            if bool(guidance.get("needs_validation_clarification", False)):
                _append_jsonl(
                    os.path.join(artifacts_root, "continuation_decisions.jsonl"),
                    {
                        "event": "clarification_requested",
                        "session_id": active_session_id,
                        "request_id": state.request_id,
                        "reason": "validation_method_required",
                    },
                )
            continuation_ctx = {
                "session_id": active_session_id,
                "parent_request_id": state.request_id,
                "iteration_index": iteration_index + 1,
                "delta_requirement": initial_delta,
                "prior_run_summary": summary,
                "trigger_type": "improvement",
                "continuation_mode": continuation_mode,
                "continuation_reason": "user_followup",
                "carry_forward_memory": True,
                "immutable_constraints": state.plan.get("constraints", []) if isinstance(state.plan, dict) else [],
                "continuation_guidance": guidance,
            }
            state.request_id = str(uuid.uuid4())
            state.plan = create_plan(
                requirement=initial_delta,
                repo_name="ai-orchestrator",
                request_id=state.request_id,
                context_store=context_store,
                ask_user=_ask_clarification,
                max_rounds=3,
                preselected_project_ref=preselected_project_ref,
                handoff_writer=DevHandoffStore(repo_root=repo_root),
                continuation_context=continuation_ctx,
            )
            _append_jsonl(
                os.path.join(artifacts_root, "continuation_decisions.jsonl"),
                {
                    "event": "continuation_completed",
                    "session_id": active_session_id,
                    "request_id": state.request_id,
                    "final_status": "plan_prepared",
                },
            )
            latest_context = context_store.get_context_by_request_id(state.request_id or "")
            loaded_handoff = latest_context.get("dev_handoff") if isinstance(latest_context, dict) else None
            loaded_request_id = state.request_id
            continue
        if bool(result.get("continuation_eligible", False)):
            print("[CONTINUATION] follow-up available but continuation mode is disabled; exiting one-shot run.")
            artifacts_root = os.path.join(repo_root, ".orchestrator")
            _append_jsonl(
                os.path.join(artifacts_root, "continuation_decisions.jsonl"),
                {
                    "event": "explicit_deactivation",
                    "session_id": active_session_id,
                    "request_id": state.request_id,
                    "continuation_mode": continuation_mode,
                    "env_flag_enabled": _env_flag(CONTINUATION_FLAG, default=True),
                },
            )
        break
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "plan", "execute"], default="full")
    parser.add_argument("--from-latest", action="store_true")
    parser.add_argument("--continuation-mode", choices=["off", "prompt", "always"], default="always")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--continue-from-request-id", default="")
    parser.add_argument("--delta-requirement", default="")
    parser.add_argument("--close-session", action="store_true")
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
                continuation_mode=args.continuation_mode,
                session_id=args.session_id,
                continue_from_request_id=args.continue_from_request_id,
                delta_requirement=args.delta_requirement,
                close_session=args.close_session,
            )
        )
    raise SystemExit(
        run(
            req,
            mode=args.mode,
            from_latest=args.from_latest,
            continuation_mode=args.continuation_mode,
            session_id=args.session_id,
            continue_from_request_id=args.continue_from_request_id,
            delta_requirement=args.delta_requirement,
            close_session=args.close_session,
        )
    )
