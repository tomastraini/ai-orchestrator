from __future__ import annotations

import tempfile
import os
import unittest

from services.dev.edit_validator import validate_intent_alignment, validate_post_apply, validate_pre_apply


class EditValidatorTests(unittest.TestCase):
    def test_pre_apply_requires_existing_for_update(self) -> None:
        result = validate_pre_apply(
            path="/tmp/missing.txt",
            modification_type="update",
            creation_policy="must_exist",
            exists_before=False,
        )
        self.assertTrue(result.get("passed"))
        self.assertIn("target_missing_before_apply", result.get("warnings", []))

    def test_post_apply_checks_syntax(self) -> None:
        result = validate_post_apply(
            path="src/main.py",
            before_content="x=1\n",
            after_content="def x(:\n",
            action="updated_file",
        )
        self.assertFalse(result.get("passed"))
        self.assertIn("syntax_sanity_failed", result.get("errors", []))

    def test_post_apply_rename_no_delta_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "main.tsx")
            result = validate_post_apply(
                path=path,
                before_content="const a = 1\n",
                after_content="const a = 1\n",
                action="renamed_file",
            )
            self.assertTrue(result.get("passed"))

    def test_intent_alignment_rejects_component_into_entrypoint(self) -> None:
        result = validate_intent_alignment(
            expected_path_hint="projects/app/src/app.component.ts",
            file_name="app.component.ts",
            details="update component behavior",
            selected_path="projects/app/src/main.ts",
        )
        self.assertFalse(result.get("passed"))
        self.assertIn("intent_target_class_mismatch", result.get("errors", []))


if __name__ == "__main__":
    unittest.main()
