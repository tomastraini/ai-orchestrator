from __future__ import annotations

import os
import tempfile
import unittest

from services.dev.handoffpack_reader import load_handoff_with_fallback
from services.pm.dev_handoff_store import DevHandoffStore, build_dev_handoff


class HandoffPackRoundTripTests(unittest.TestCase):
    def test_yaml_roundtrip_fallback_loader(self) -> None:
        plan = {
            "summary": "Create calculator app",
            "project_mode": "new_project",
            "project_ref": {"name": "calculator", "path_hint": "projects/calculator"},
            "stack": {
                "frontend": "React",
                "backend": None,
                "language_preferences": ["TypeScript"],
            },
            "pm_checklist": {
                "project_scope": "new_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [
                {
                    "cwd": "projects/calculator",
                    "command": "npx create-react-app front-end --template typescript",
                    "purpose": "scaffold frontend",
                }
            ],
            "target_files": [
                {
                    "file_name": "App.tsx",
                    "expected_path_hint": "projects/calculator/front-end/src/App.tsx",
                    "modification_type": "update",
                    "details": "implement calculator",
                }
            ],
            "constraints": ["Use TypeScript"],
            "validation": ["npm run build"],
            "clarification_summary": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            handoff = build_dev_handoff(
                request_id="req-handoffpack-1",
                plan=plan,
                rounds=[{"question": "Project type?", "answer": "new"}],
            )
            store = DevHandoffStore(repo_root=tmp)
            store.write_latest(handoff)
            json_path = os.path.join(tmp, ".orchestrator", "dev_handoff.json")
            yaml_path = json_path.replace(".json", ".yaml")
            self.assertTrue(os.path.exists(yaml_path), msg=yaml_path)

            loaded = load_handoff_with_fallback(json_path)
            self.assertIsNotNone(loaded)
            self.assertEqual(str(loaded.get("project_root")), "projects/calculator")
            self.assertIn("execution_steps", loaded)


if __name__ == "__main__":
    unittest.main()

