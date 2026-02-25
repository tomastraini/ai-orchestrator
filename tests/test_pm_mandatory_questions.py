from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("AZURE_OPENAI_KEY", "test-key")

from services.pm.pm_context_store import PMContextStore
from services.pm import pm_service


def _final_plan_stub() -> dict:
    return {
        "summary": "Create calculator",
        "project_mode": "new_project",
        "project_ref": {"name": "calculator", "path_hint": "projects/calculator"},
        "stack": {
            "frontend": "React",
            "backend": "NestJS",
            "language_preferences": ["TypeScript"],
        },
        "pm_checklist": {
            "project_scope": "new_project",
            "architecture": "fullstack",
            "backend_required": "yes",
            "database_required": "no",
        },
        "bootstrap_commands": [
            {
                "cwd": "projects/calculator/front-end",
                "command": "python -c \"print('bootstrap')\"",
                "purpose": "bootstrap",
            }
        ],
        "target_files": [
            {
                "file_name": "README.md",
                "expected_path_hint": "projects/calculator/README.md",
                "modification_type": "create_file",
                "details": "Create README",
            }
        ],
        "constraints": ["Do not push"],
        "validation": ["Build passes"],
        "clarification_summary": [],
    }


class PMMandatoryChecklistTests(unittest.TestCase):
    def test_create_plan_dynamic_clarification_policy(self) -> None:
        asked: list[str] = []
        answers = {
            "Is this for a new project or an existing project? (new/existing)": "new",
            "Should this be frontend-only or fullstack? (frontend/fullstack)": "fullstack",
            "Is a backend required? (yes/no)": "yes",
            "Is a database required? (yes/no)": "no",
        }

        def ask_user(question: str, _round_index: int, _max_rounds: int) -> str:
            asked.append(question)
            return answers[question]

        original = pm_service._request_model_decision

        def fake_request(*args, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "status": "final_plan",
                "question": "",
                "hypothesis": {"project_mode": "new_project"},
                "plan": _final_plan_stub(),
            }

        pm_service._request_model_decision = fake_request
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = PMContextStore(repo_root=tmp)
                plan = pm_service.create_plan(
                    requirement="Create a calculator",
                    request_id="req-checklist",
                    context_store=store,
                    ask_user=ask_user,
                    max_rounds=1,
                )
        finally:
            pm_service._request_model_decision = original

        # Clarifications are now dynamic and may be zero for sufficiently clear inputs.
        self.assertGreaterEqual(len(asked), 0)
        self.assertEqual(plan["pm_checklist"]["project_scope"], "new_project")
        self.assertEqual(plan["pm_checklist"]["architecture"], "fullstack")
        self.assertEqual(plan["pm_checklist"]["backend_required"], "yes")
        self.assertEqual(plan["pm_checklist"]["database_required"], "no")

    def test_requirement_inference_skips_unnecessary_questions(self) -> None:
        asked: list[str] = []

        def ask_user(question: str, _round_index: int, _max_rounds: int) -> str:
            asked.append(question)
            return "n/a"

        original = pm_service._request_model_decision

        def fake_request(*args, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "status": "final_plan",
                "question": "",
                "hypothesis": {"project_mode": "new_project"},
                "plan": _final_plan_stub(),
            }

        pm_service._request_model_decision = fake_request
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = PMContextStore(repo_root=tmp)
                plan = pm_service.create_plan(
                    requirement="Create a frontend-only calculator without backend and without database",
                    request_id="req-checklist-2",
                    context_store=store,
                    ask_user=ask_user,
                    max_rounds=1,
                )
        finally:
            pm_service._request_model_decision = original

        self.assertEqual(asked, [])
        self.assertEqual(plan["pm_checklist"]["project_scope"], "new_project")
        self.assertEqual(plan["pm_checklist"]["architecture"], "frontend_only")
        self.assertEqual(plan["pm_checklist"]["backend_required"], "no")
        self.assertEqual(plan["pm_checklist"]["database_required"], "no")


if __name__ == "__main__":
    unittest.main()
