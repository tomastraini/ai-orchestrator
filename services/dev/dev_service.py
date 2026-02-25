from __future__ import annotations

import os
import uuid
from typing import Any, Callable, Dict, Optional

from services.dev.dev_master_graph import DevMasterGraph


DevAskFn = Callable[[str], str]
LLMCorrectorFn = Callable[[Dict[str, Any]], str]


class DevService:
    def __init__(self, scope_root: str):
        self.scope_root = scope_root
        self.graph = DevMasterGraph()

    def execute_plan(
        self,
        plan: Dict[str, Any],
        *,
        request_id: Optional[str] = None,
        ask_user: Optional[DevAskFn] = None,
        handoff: Optional[Dict[str, Any]] = None,
        llm_corrector: Optional[LLMCorrectorFn] = None,
    ) -> Dict[str, Optional[str]]:
        """
        Execute PM-authored plan in a linear developer workflow.
        """
        os.makedirs(self.scope_root, exist_ok=True)
        if llm_corrector is None:
            llm_corrector = self._default_llm_corrector
        final_state = self.graph.run(
            request_id=request_id or str(uuid.uuid4()),
            plan=plan,
            scope_root=self.scope_root,
            ask_user=ask_user,
            handoff=handoff,
            llm_corrector=llm_corrector,
        )

        logs = final_state.get("logs", [])
        errors = final_state.get("errors", [])
        build_logs = "\n".join(str(x) for x in logs + errors).strip() or None

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
        try:
            response = client.responses.create(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.1-codex-mini"),
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": str(payload)},
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
