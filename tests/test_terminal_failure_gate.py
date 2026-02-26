from __future__ import annotations

import json
import os
import tempfile
import unittest

from services.dev.dev_master_graph import DevMasterGraph


class TerminalFailureGateTests(unittest.TestCase):
    def _base_plan(self) -> dict:
        return {
            "summary": "Validate graceful degradation on missing capability.",
            "project_mode": "new_project",
            "project_ref": {"name": "cap-gap", "path_hint": "projects/cap-gap"},
            "stack": {"frontend": "Node", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "new_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [
                {
                    "file_name": "package.json",
                    "expected_path_hint": "projects/cap-gap/package.json",
                    "modification_type": "verify",
                    "details": "package must exist",
                    "creation_policy": "must_exist",
                }
            ],
            "constraints": [],
            "validation": [],
            "clarification_summary": [],
        }

    def test_missing_toolchain_degrades_without_implementation_failed(self) -> None:
        graph = DevMasterGraph()
        plan = self._base_plan()
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = os.path.join(tmp, "cap-gap")
            os.makedirs(project_dir, exist_ok=True)
            with open(os.path.join(project_dir, "package.json"), "w", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "name": "cap-gap",
                            "version": "1.0.0",
                            "scripts": {"build": "nest build"},
                        }
                    )
                )
            state = graph.run(
                request_id="terminal-gate-capability-1",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
            )
        self.assertEqual(state.get("final_compile_status"), "failed")
        self.assertNotEqual(state.get("status"), "implementation_failed", msg=str(state.get("errors", [])))
        events = [event for event in state.get("telemetry_events", []) if isinstance(event, dict)]
        self.assertTrue(
            any(str(event.get("category", "")) == "deterministic_failure_signatures" for event in events),
            msg=str(events),
        )


if __name__ == "__main__":
    unittest.main()
