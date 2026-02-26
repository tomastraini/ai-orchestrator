from __future__ import annotations

import tempfile
import unittest
import os

from services.dev.dev_master_graph import DevMasterGraph


class DevPreflightPlanningTests(unittest.TestCase):
    def test_preflight_phase_populates_plan(self) -> None:
        graph = DevMasterGraph()
        plan = {
            "summary": "Build app",
            "project_mode": "new_project",
            "project_ref": {"name": "calc", "path_hint": "projects/calc"},
            "stack": {"frontend": "React", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "new_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [],
            "constraints": ["none"],
            "validation": ["npm run build"],
            "clarification_summary": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "calc"), exist_ok=True)
            with open(os.path.join(tmp, "calc", "package.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            state = graph.run(request_id="preflight-1", plan=plan, scope_root=tmp, ask_user=lambda _: "n/a")
        self.assertIn("dev_preflight_plan", state)
        self.assertEqual(state["phase_status"]["dev_preflight_planning"], "completed")


if __name__ == "__main__":
    unittest.main()

