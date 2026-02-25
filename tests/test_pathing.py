from __future__ import annotations

import unittest

from shared.pathing import canonical_projects_path


class PathingTests(unittest.TestCase):
    def test_canonical_projects_path_collapses_nested_projects(self) -> None:
        path = canonical_projects_path(
            "projects/calculator/projects/calculator",
            "projects/calculator",
        )
        self.assertEqual(path, "projects/calculator")


if __name__ == "__main__":
    unittest.main()

