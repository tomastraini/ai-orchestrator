from __future__ import annotations

import os
import unittest

from shared.schemas import validate_plan_json

os.environ.setdefault("AZURE_OPENAI_KEY", "test-key")
from services.pm.pm_service import _normalize_new_project_plan


def _base_plan_new_project() -> dict:
    return {
        "summary": "Create a scientific calculator app with React and NestJS.",
        "project_mode": "new_project",
        "project_ref": {"name": "scientific-calculator", "path_hint": "projects/scientific-calculator"},
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
                "cwd": "projects",
                "command": "npx create-react-app scientific-calculator-frontend --template typescript",
                "purpose": "Create frontend scaffold",
            },
            {
                "cwd": "projects",
                "command": "nest new scientific-calculator-backend",
                "purpose": "Create backend scaffold",
            },
        ],
        "target_files": [
            {
                "file_name": "README.md",
                "expected_path_hint": "projects/scientific-calculator",
                "modification_type": "create",
                "details": "Create project bootstrap notes",
            }
        ],
        "constraints": ["Prefer TypeScript defaults", "Do not overwrite full files"],
        "validation": ["Frontend builds", "Backend tests pass"],
        "clarification_summary": [],
    }


class PlanSchemaTests(unittest.TestCase):
    def test_vague_requirement_defaults_to_react_nest_typescript_is_valid(self) -> None:
        plan = _base_plan_new_project()
        ok, errors = validate_plan_json(plan, requirement="Create a simple calculator")
        self.assertTrue(ok, msg=f"Expected valid plan, got: {errors}")

    def test_existing_project_plan_can_have_empty_bootstrap_commands(self) -> None:
        plan = _base_plan_new_project()
        plan["project_mode"] = "existing_project"
        plan["project_ref"] = {"name": "clinigma-ui", "path_hint": "projects/clinigma-ui"}
        plan["bootstrap_commands"] = []
        ok, errors = validate_plan_json(plan, requirement="Update existing clinigma-ui calculator widget")
        self.assertTrue(ok, msg=f"Expected valid existing-project plan, got: {errors}")

    def test_ambiguous_case_can_include_clarification_summary(self) -> None:
        plan = _base_plan_new_project()
        plan["clarification_summary"] = [
            "Q: Is this a new app or existing app? | A: New app.",
            "Q: Do you want backend APIs? | A: Yes, NestJS.",
        ]
        ok, errors = validate_plan_json(plan, requirement="Build calculator")
        self.assertTrue(ok, msg=f"Expected valid plan with clarifications, got: {errors}")

    def test_unknown_key_is_rejected(self) -> None:
        plan = _base_plan_new_project()
        plan["extra"] = "not-allowed"
        ok, errors = validate_plan_json(plan, requirement="Create calculator app")
        self.assertFalse(ok)
        self.assertTrue(any("unknown keys" in e for e in errors), msg=str(errors))

    def test_new_project_requires_bootstrap_commands(self) -> None:
        plan = _base_plan_new_project()
        plan["bootstrap_commands"] = []
        ok, errors = validate_plan_json(plan, requirement="Create calculator app")
        self.assertFalse(ok)
        self.assertTrue(
            any("bootstrap_commands" in e and "new_project" in e for e in errors),
            msg=str(errors),
        )

    def test_new_project_requires_projects_root_path_hint(self) -> None:
        plan = _base_plan_new_project()
        plan["project_ref"]["path_hint"] = "somewhere-else/calculator"
        ok, errors = validate_plan_json(plan, requirement="Create calculator app")
        self.assertFalse(ok)
        self.assertTrue(any("project_ref.path_hint" in e for e in errors), msg=str(errors))

    def test_pm_normalizes_empty_target_files_for_new_project(self) -> None:
        plan = _base_plan_new_project()
        plan["target_files"] = []
        normalized = _normalize_new_project_plan(plan)
        ok, errors = validate_plan_json(normalized, requirement="Create calculator app")
        self.assertTrue(ok, msg=str(errors))
        self.assertGreaterEqual(len(normalized["target_files"]), 2)
        for target in normalized["target_files"]:
            self.assertTrue(
                str(target["expected_path_hint"]).startswith("projects/"),
                msg=str(target),
            )


if __name__ == "__main__":
    unittest.main()
