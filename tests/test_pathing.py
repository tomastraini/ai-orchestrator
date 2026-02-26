from __future__ import annotations

import unittest

from shared.pathing import canonical_projects_path, canonicalize_scope_path, collapse_duplicate_tail_segments


class PathingTests(unittest.TestCase):
    def test_canonical_projects_path_collapses_nested_projects(self) -> None:
        path = canonical_projects_path(
            "projects/calculator/projects/calculator",
            "projects/calculator",
        )
        self.assertEqual(path, "projects/calculator")

    def test_canonical_projects_path_defaults_when_invalid(self) -> None:
        path = canonical_projects_path("not-projects/calc", "projects/calc")
        self.assertEqual(path, "projects/calc")

    def test_collapse_duplicate_tail_segments(self) -> None:
        self.assertEqual(
            collapse_duplicate_tail_segments("calc/calc/src/src/app.ts"),
            "calc/src/app.ts",
        )

    def test_canonicalize_scope_path_keeps_scope_and_normalizes(self) -> None:
        scope = "C:/work/repo"
        candidate = "C:/work/repo/projects/app/projects/app/src/main.ts"
        canonical = canonicalize_scope_path(scope, candidate).replace("\\", "/")
        self.assertIn("/projects/app/src/main.ts", canonical)


if __name__ == "__main__":
    unittest.main()

