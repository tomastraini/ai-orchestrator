from __future__ import annotations

import tempfile
import unittest

from services.dev_master_graph import DevMasterGraph


class ValidationGateByStackTests(unittest.TestCase):
    def test_validation_failure_blocks_completed_status(self) -> None:
        graph = DevMasterGraph()
        plan = {
            "summary": "Validation should fail",
            "project_mode": "new_project",
            "project_ref": {"name": "calc", "path_hint": "projects/calc"},
            "stack": {"frontend": "Generic", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "new_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [],
            "constraints": ["none"],
            "validation": ["python -c \"import sys; sys.exit(1)\""],
            "clarification_summary": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(request_id="val-fail", plan=plan, scope_root=tmp, ask_user=lambda _: "n/a")
        self.assertNotEqual(state["status"], "completed")
        self.assertEqual(state.get("validation_status"), "failed")


if __name__ == "__main__":
    unittest.main()

