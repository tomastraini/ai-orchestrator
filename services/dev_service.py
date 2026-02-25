# services/dev_service.py

from __future__ import annotations

import os
import uuid
from typing import Any, Callable, Dict, Optional

from services.dev_master_graph import DevMasterGraph


DevAskFn = Callable[[str], str]


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
    ) -> Dict[str, Optional[str]]:
        """
        Execute PM-authored plan in a linear developer workflow.
        """
        os.makedirs(self.scope_root, exist_ok=True)
        final_state = self.graph.run(
            request_id=request_id or str(uuid.uuid4()),
            plan=plan,
            scope_root=self.scope_root,
            ask_user=ask_user,
        )

        logs = final_state.get("logs", [])
        errors = final_state.get("errors", [])
        build_logs = "\n".join(str(x) for x in logs + errors).strip() or None

        return {
            "branch_name": None,
            "build_logs": build_logs,
            "status": str(final_state.get("status", "unknown")),
        }
