from __future__ import annotations

import json
import os
import tempfile
import unittest

from services.pm.dev_handoff_store import DevHandoffStore, build_dev_handoff
from services.pm.pm_context_store import PMContextStore


class PMHandoffStoreTests(unittest.TestCase):
    def _sample_plan(self) -> dict:
        return {
            "summary": "Create calculator app",
            "project_mode": "new_project",
            "project_ref": {"name": "calculator", "path_hint": "projects/calculator"},
            "stack": {
                "frontend": "React",
                "backend": "NestJS",
                "language_preferences": ["TypeScript"],
            },
            "pm_checklist": {
                "project_scope": "new_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [
                {"cwd": "projects/calculator", "command": "echo bootstrap", "purpose": "bootstrap"}
            ],
            "target_files": [
                {
                    "file_name": "front-end",
                    "expected_path_hint": "projects/calculator/front-end",
                    "modification_type": "create_directory",
                    "details": "Create frontend folder",
                }
            ],
            "constraints": ["Use TypeScript"],
            "validation": ["Frontend builds"],
            "clarification_summary": [],
        }

    def test_handoff_persisted_in_context_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request_id = "req-handoff-1"
            rounds = [{"question": "new or existing?", "answer": "new"}]
            plan = self._sample_plan()

            handoff = build_dev_handoff(request_id=request_id, plan=plan, rounds=rounds)

            context = PMContextStore(repo_root=tmp)
            context.load_context(request_id=request_id, original_requirement="calculator")
            context.save_final_plan(request_id=request_id, plan=plan)
            context.save_dev_handoff(request_id=request_id, handoff=handoff)

            DevHandoffStore(repo_root=tmp).write_latest(handoff)

            latest = context.load_context(request_id=request_id)
            self.assertEqual(latest["dev_handoff"]["request_id"], request_id)
            self.assertEqual(latest["dev_handoff"]["project_root"], "projects/calculator")
            structure_paths = [
                str(x.get("path", "")) for x in latest["dev_handoff"].get("structure_plan", [])
            ]
            self.assertIn("projects/calculator", structure_paths)

            handoff_path = os.path.join(tmp, ".orchestrator", "dev_handoff.json")
            self.assertTrue(os.path.exists(handoff_path))
            with open(handoff_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["latest_handoff"]["request_id"], request_id)
            self.assertIn("internal_checklist", payload["latest_handoff"])
            self.assertIn("task_outcomes", payload["latest_handoff"])
            self.assertIn("target_file_metadata", payload["latest_handoff"])

    def test_handoff_carries_optional_target_cognition_fields(self) -> None:
        plan = self._sample_plan()
        plan["target_files"] = [
            {
                "file_name": "src/main.tsx",
                "expected_path_hint": "projects/calculator/src/main.tsx",
                "modification_type": "modify",
                "details": "entrypoint update",
                "creation_policy": "must_exist",
                "symbol_hints": ["App", "createRoot"],
                "candidate_paths": [{"path": "projects/calculator/src/main.tsx", "score": 0.9}],
                "path_confidence": 0.9,
                "entrypoint_candidate": True,
            }
        ]
        handoff = build_dev_handoff(request_id="req-handoff-cognition-1", plan=plan, rounds=[])
        metadata = handoff.get("target_file_metadata", [])
        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0].get("file_name"), "src/main.tsx")
        self.assertTrue(metadata[0].get("entrypoint_candidate"))

    def test_handoff_normalizes_redundant_vite_target_path(self) -> None:
        plan = self._sample_plan()
        plan["bootstrap_commands"] = [
            {
                "cwd": "projects/calculator",
                "command": "npm create vite@latest projects/calculator -- --template react-ts",
                "purpose": "bootstrap frontend",
            }
        ]
        handoff = build_dev_handoff(request_id="req-handoff-2", plan=plan, rounds=[])
        self.assertEqual(len(handoff.get("execution_steps", [])), 1)
        step = handoff["execution_steps"][0]
        self.assertEqual(step["cwd"], "projects/calculator")
        self.assertIn("npm create vite@latest .", step["command"])

    def test_handoff_normalizes_redundant_vite_target_path_with_npm_init(self) -> None:
        plan = self._sample_plan()
        plan["bootstrap_commands"] = [
            {
                "cwd": "projects/calculator",
                "command": "npm init vite@latest calculator -- --template react",
                "purpose": "bootstrap frontend",
            }
        ]
        handoff = build_dev_handoff(request_id="req-handoff-3", plan=plan, rounds=[])
        self.assertEqual(len(handoff.get("execution_steps", [])), 1)
        step = handoff["execution_steps"][0]
        self.assertTrue(step["command"].startswith("npm init vite@latest"))

    def test_handoff_normalizes_empty_cwd_for_scaffold_target(self) -> None:
        plan = self._sample_plan()
        plan["bootstrap_commands"] = [
            {
                "cwd": "",
                "command": "npx create-react-app projects/calculator --template typescript",
                "purpose": "bootstrap frontend",
            }
        ]
        handoff = build_dev_handoff(request_id="req-handoff-4", plan=plan, rounds=[])
        self.assertEqual(len(handoff.get("execution_steps", [])), 1)
        step = handoff["execution_steps"][0]
        self.assertEqual(step["cwd"], "projects")


if __name__ == "__main__":
    unittest.main()
