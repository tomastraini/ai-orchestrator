from __future__ import annotations

import os
import tempfile
import unittest

from services.pm.project_resolver import (
    is_vague_existing_project_request,
    resolve_project_candidates,
    select_top_candidate,
)


class ProjectResolverTests(unittest.TestCase):
    def test_detects_vague_existing_request(self) -> None:
        self.assertTrue(is_vague_existing_project_request("Improve calculator UX"))
        self.assertFalse(is_vague_existing_project_request("Create a brand new calculator app"))

    def test_ranks_and_selects_best_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calc = os.path.join(tmp, "calculator")
            blog = os.path.join(tmp, "blog")
            os.makedirs(calc, exist_ok=True)
            os.makedirs(blog, exist_ok=True)
            with open(os.path.join(calc, "package.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            with open(os.path.join(calc, "tsconfig.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")

            candidates = resolve_project_candidates("Please improve calculator", tmp)
            self.assertGreaterEqual(len(candidates), 2)
            self.assertEqual(candidates[0]["name"], "calculator")

            top = select_top_candidate("Improve calculator behavior", tmp)
            self.assertIsNotNone(top)
            self.assertEqual(top["path_hint"], "projects/calculator")


if __name__ == "__main__":
    unittest.main()
