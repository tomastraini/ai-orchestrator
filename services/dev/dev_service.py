from __future__ import annotations

import json
import os
import uuid
from typing import Any, Callable, Dict, Optional

from services.dev.intent_router import INTENT_ANALYSIS, INTENT_ARTIFACT, route_plan_intent
from services.dev.dev_master_graph import DevMasterGraph
from shared.handoff_ports import HandoffWriter


DevAskFn = Callable[[str], str]
LLMCorrectorFn = Callable[[Dict[str, Any]], str]
LogSinkFn = Callable[[str], None]


class DevService:
    def __init__(self, scope_root: str, max_model_calls_per_run: int = 1):
        self.scope_root = scope_root
        self.graph = DevMasterGraph()
        self.max_model_calls_per_run = max(0, int(max_model_calls_per_run))

    def execute_plan(
        self,
        plan: Dict[str, Any],
        *,
        request_id: Optional[str] = None,
        ask_user: Optional[DevAskFn] = None,
        handoff: Optional[Dict[str, Any]] = None,
        llm_corrector: Optional[LLMCorrectorFn] = None,
        max_model_calls_per_run: Optional[int] = None,
        log_sink: Optional[LogSinkFn] = None,
        handoff_writer: Optional[HandoffWriter] = None,
        run_artifacts_root: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        """
        Execute PM-authored plan in a linear developer workflow.
        """
        os.makedirs(self.scope_root, exist_ok=True)
        if callable(log_sink):
            log_sink("[DEV] starting graph run...")
        intent = route_plan_intent(plan)
        if llm_corrector is None:
            llm_corrector = self._default_llm_corrector
        effective_request_id = request_id or str(uuid.uuid4())
        if intent in {INTENT_ANALYSIS, INTENT_ARTIFACT} and not plan.get("target_files") and not plan.get("bootstrap_commands"):
            final_state: Dict[str, Any] = {
                "status": "completed",
                "phase_status": {"intent_router": "completed"},
                "errors": [],
                "logs": [f"[INTENT] routed={intent}", "[INTENT] execution pipeline skipped"],
                "telemetry_events": [{"category": "intent_router", "intent": intent}],
                "task_outcomes": [],
                "checklist_cursor": "",
                "internal_checklist": [],
                "final_summary": str(plan.get("summary", "")),
                "active_root_file_index": {},
            }
        else:
            final_state = self.graph.run(
                request_id=effective_request_id,
                plan=plan,
                scope_root=self.scope_root,
                ask_user=ask_user,
                handoff=handoff,
                llm_corrector=llm_corrector,
                max_model_calls_per_run=(
                    self.max_model_calls_per_run
                    if max_model_calls_per_run is None
                    else max(0, int(max_model_calls_per_run))
                ),
                log_sink=log_sink,
            )
        self._persist_run_artifacts(
            final_state,
            effective_request_id,
            artifacts_root=run_artifacts_root,
        )

        logs = final_state.get("logs", [])
        errors = final_state.get("errors", [])
        build_logs = "\n".join(str(x) for x in logs + errors).strip() or None
        if isinstance(handoff, dict):
            updated_handoff = dict(handoff)
            internal_checklist = final_state.get("internal_checklist", [])
            updated_handoff["internal_checklist"] = internal_checklist if isinstance(internal_checklist, list) else []
            updated_handoff["checklist_cursor"] = str(final_state.get("checklist_cursor", ""))
            task_outcomes = final_state.get("task_outcomes", [])
            updated_handoff["task_outcomes"] = task_outcomes if isinstance(task_outcomes, list) else []
            updated_handoff["dev_preflight_plan"] = final_state.get("dev_preflight_plan", {})
            updated_handoff["memory"] = final_state.get("repository_memory", {})
            pending_ids: list[str] = []
            if isinstance(internal_checklist, list):
                for item in internal_checklist:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("status", "")) != "completed":
                        pending_ids.append(str(item.get("id", "")))
            updated_handoff["pending_tasks"] = [x for x in pending_ids if x]
            if handoff_writer is not None:
                handoff_writer.write_latest(updated_handoff)

        return {
            "branch_name": None,
            "build_logs": build_logs,
            "status": str(final_state.get("status", "unknown")),
        }

    def _persist_run_artifacts(
        self,
        final_state: Dict[str, Any],
        request_id: str,
        *,
        artifacts_root: Optional[str] = None,
    ) -> None:
        repo_root = os.path.dirname(self.scope_root.rstrip(os.sep))
        root = artifacts_root or os.path.join(repo_root, ".orchestrator", "runs")
        run_dir = os.path.join(root, request_id)
        os.makedirs(run_dir, exist_ok=True)
        events = final_state.get("telemetry_events", [])
        outcomes = final_state.get("task_outcomes", [])
        summary = {
            "request_id": request_id,
            "status": final_state.get("status", "unknown"),
            "phase_status": final_state.get("phase_status", {}),
            "errors_count": len(final_state.get("errors", [])),
            "errors": final_state.get("errors", []),
            "checklist_cursor": final_state.get("checklist_cursor", ""),
            "checklist_items": final_state.get("internal_checklist", []),
            "final_summary": final_state.get("final_summary", ""),
        }
        metrics = {
            "target_resolution_success_rate": _compute_target_resolution_success_rate(final_state),
            "checklist_completion_rate": _compute_checklist_completion_rate(final_state),
            "unresolved_target_errors": _count_unresolved_target_errors(final_state),
            "retry_count": int(final_state.get("retry_count", 0) or 0),
            "task_outcome_count": len(outcomes if isinstance(outcomes, list) else []),
        }
        cognition = (
            final_state.get("active_root_file_index", {}).get("cognition", {})
            if isinstance(final_state.get("active_root_file_index"), dict)
            else {}
        )
        try:
            with open(os.path.join(run_dir, "events.jsonl"), "w", encoding="utf-8") as fh:
                for event in events if isinstance(events, list) else []:
                    if not isinstance(event, dict):
                        continue
                    fh.write(json.dumps(event, sort_keys=True) + "\n")
            with open(os.path.join(run_dir, "task_outcomes.json"), "w", encoding="utf-8") as fh:
                json.dump(outcomes if isinstance(outcomes, list) else [], fh, indent=2)
            with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2)
            with open(os.path.join(run_dir, "cognition_index.json"), "w", encoding="utf-8") as fh:
                json.dump(cognition if isinstance(cognition, dict) else {}, fh, indent=2)
            with open(os.path.join(run_dir, "metrics.json"), "w", encoding="utf-8") as fh:
                json.dump(metrics, fh, indent=2)
        except Exception:
            # Artifact persistence should never fail the execution path.
            pass

    @staticmethod
    def _default_llm_corrector(payload: Dict[str, Any]) -> str:
        """
        Lazy Azure-based command correction to avoid importing config at module import time.
        """
        try:
            from config import client  # lazy import
        except Exception:
            return ""

        if not hasattr(client, "responses"):
            return ""

        prompt = (
            "You are a senior build/debug agent. "
            "Return ONLY one corrected shell command string. "
            "No markdown, no explanation. "
            "Constraints: command must run within ./projects scope, no git push."
        )
        reduced_payload = {
            "task_id": str(payload.get("task_id", "")),
            "cwd": str(payload.get("cwd", "")),
            "command": str(payload.get("command", "")),
            "error": str(payload.get("error", "")),
            "last_attempt": payload.get("last_attempt", {}),
            "scope_constraint": str(payload.get("scope_constraint", "")),
            "push_constraint": str(payload.get("push_constraint", "")),
        }

        try:
            response = client.responses.create(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.1-codex-mini"),
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": str(reduced_payload)},
                ],
            )
            for item in response.output:
                if item.type != "message":
                    continue
                for part in item.content:
                    if part.type == "output_text":
                        return part.text.strip()
        except Exception:
            return ""
        return ""


def _compute_checklist_completion_rate(final_state: Dict[str, Any]) -> float:
    checklist = final_state.get("internal_checklist", [])
    if not isinstance(checklist, list) or not checklist:
        return 1.0
    completed = 0
    total = 0
    for item in checklist:
        if not isinstance(item, dict):
            continue
        total += 1
        if str(item.get("status", "")) == "completed":
            completed += 1
    if total <= 0:
        return 1.0
    return completed / total


def _count_unresolved_target_errors(final_state: Dict[str, Any]) -> int:
    errors = final_state.get("errors", [])
    if not isinstance(errors, list):
        return 0
    return sum(1 for err in errors if "Expected target missing and discovery failed" in str(err))


def _compute_target_resolution_success_rate(final_state: Dict[str, Any]) -> float:
    targets = final_state.get("implementation_targets", [])
    if not isinstance(targets, list) or not targets:
        return 1.0
    unresolved = _count_unresolved_target_errors(final_state)
    resolved = max(0, len(targets) - unresolved)
    return resolved / max(1, len(targets))
