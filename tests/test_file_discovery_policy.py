from __future__ import annotations

import os
import tempfile
import unittest

from services.dev.dev_master_graph import DevMasterGraph


class FileDiscoveryPolicyTests(unittest.TestCase):
    def test_update_missing_path_discovers_existing_file(self) -> None:
        graph = DevMasterGraph()
        plan = {
            "summary": "Update app",
            "project_mode": "existing_project",
            "project_ref": {"name": "calc", "path_hint": "projects/calc"},
            "stack": {"frontend": "React", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "existing_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [
                {
                    "file_name": "App.tsx",
                    "expected_path_hint": "projects/calc/src/App.tsx",
                    "modification_type": "update",
                    "details": "update ui",
                    "creation_policy": "must_exist",
                }
            ],
            "constraints": ["none"],
            "validation": ["python -c \"print('ok')\""],
            "clarification_summary": [],
        }
        handoff = {"project_root": "projects/calc"}
        with tempfile.TemporaryDirectory() as tmp:
            actual_root = os.path.join(tmp, "calc", "projects", "calc", "src")
            os.makedirs(actual_root, exist_ok=True)
            with open(os.path.join(actual_root, "App.tsx"), "w", encoding="utf-8") as fh:
                fh.write("export default function App(){return null}")
            state = graph.run(
                request_id="discover-1",
                plan=plan,
                scope_root=tmp,
                handoff=handoff,
                ask_user=lambda _: "n/a",
            )
        self.assertEqual(state["status"], "completed", msg=str(state.get("errors")))

    def test_directory_hint_plus_filename_resolves_real_file(self) -> None:
        graph = DevMasterGraph()
        plan = {
            "summary": "Update app",
            "project_mode": "existing_project",
            "project_ref": {"name": "calc", "path_hint": "projects/calc"},
            "stack": {"frontend": "React", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "existing_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [
                {
                    "file_name": "src/App.tsx",
                    "expected_path_hint": "projects/calc",
                    "modification_type": "update",
                    "details": "update ui",
                    "creation_policy": "must_exist",
                }
            ],
            "constraints": ["none"],
            "validation": ["python -c \"print('ok')\""],
            "clarification_summary": [],
        }
        handoff = {"project_root": "projects/calc"}
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "calc", "src"), exist_ok=True)
            with open(os.path.join(tmp, "calc", "src", "App.tsx"), "w", encoding="utf-8") as fh:
                fh.write("export default function App(){return null}")
            state = graph.run(
                request_id="discover-2",
                plan=plan,
                scope_root=tmp,
                handoff=handoff,
                ask_user=lambda _: "n/a",
            )
        self.assertEqual(state["status"], "completed", msg=str(state.get("errors")))


if __name__ == "__main__":
    unittest.main()

