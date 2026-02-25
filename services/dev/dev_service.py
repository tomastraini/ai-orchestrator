from __future__ import annotations

import os
import uuid
from typing import Any, Callable, Dict, Optional

from services.dev.dev_master_graph import DevMasterGraph
from services.pm.dev_handoff_store import DevHandoffStore


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
    ) -> Dict[str, Optional[str]]:
        """
        Execute PM-authored plan in a linear developer workflow.
        """
        os.makedirs(self.scope_root, exist_ok=True)
        if callable(log_sink):
            log_sink("[DEV] starting graph run...")
        if llm_corrector is None:
            llm_corrector = self._default_llm_corrector
        final_state = self.graph.run(
            request_id=request_id or str(uuid.uuid4()),
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
            pending_ids: list[str] = []
            if isinstance(internal_checklist, list):
                for item in internal_checklist:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("status", "")) != "completed":
                        pending_ids.append(str(item.get("id", "")))
            updated_handoff["pending_tasks"] = [x for x in pending_ids if x]
            repo_root = os.path.dirname(self.scope_root.rstrip(os.sep))
            DevHandoffStore(repo_root=repo_root).write_latest(updated_handoff)

        return {
            "branch_name": None,
            "build_logs": build_logs,
            "status": str(final_state.get("status", "unknown")),
        }

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
