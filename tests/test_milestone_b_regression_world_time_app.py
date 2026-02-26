from __future__ import annotations

import os
import tempfile
import unittest

from services.dev.dev_master_graph import DevMasterGraph


class MilestoneBRegressionWorldTimeAppTests(unittest.TestCase):
    def test_rename_target_is_applied_as_rename(self) -> None:
        graph = DevMasterGraph()
        plan = {
            "summary": "Fix jsx entrypoint",
            "project_mode": "existing_project",
            "project_ref": {"name": "world-time-app", "path_hint": "projects/world-time-app"},
            "stack": {"frontend": "React + Vite", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "existing_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [
                {
                    "file_name": "main.ts",
                    "expected_path_hint": "projects/world-time-app/src",
                    "modification_type": "rename",
                    "details": "Rename main.ts to main.tsx to enable TSX parsing",
                    "creation_policy": "must_exist",
                }
            ],
            "constraints": ["none"],
            "validation": ["python -c \"print('ok')\""],
            "clarification_summary": [],
        }
        handoff = {"project_root": "projects/world-time-app"}
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = os.path.join(tmp, "world-time-app", "src")
            os.makedirs(src_dir, exist_ok=True)
            old_path = os.path.join(src_dir, "main.ts")
            new_path = os.path.join(src_dir, "main.tsx")
            with open(old_path, "w", encoding="utf-8") as fh:
                fh.write("const app = 1\n")
            state = graph.run(
                request_id="reg-world-time-1",
                plan=plan,
                scope_root=tmp,
                handoff=handoff,
                ask_user=lambda _: "n/a",
            )
            self.assertTrue(os.path.exists(new_path), msg=str(state.get("errors", [])))
            self.assertFalse(os.path.exists(old_path), msg=str(state.get("errors", [])))
        self.assertEqual(state.get("status"), "completed", msg=str(state.get("errors", [])))


if __name__ == "__main__":
    unittest.main()
