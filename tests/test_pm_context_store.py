from __future__ import annotations

import tempfile
import unittest

from services.pm_context_store import PMContextStore


class PMContextStoreTests(unittest.TestCase):
    def test_keeps_only_single_latest_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PMContextStore(repo_root=tmp)
            first = store.load_context("req-1", "first requirement")
            self.assertEqual(first["request_id"], "req-1")

            second = store.load_context("req-2", "second requirement")
            self.assertEqual(second["request_id"], "req-2")

            loaded_first_again = store.load_context("req-1", "first requirement")
            # Since store is single-latest, loading req-1 again recreates it and
            # implies req-2 was replaced.
            self.assertEqual(loaded_first_again["request_id"], "req-1")
            self.assertEqual(loaded_first_again["rounds"], [])

    def test_rounds_and_plan_saved_on_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PMContextStore(repo_root=tmp)
            store.load_context("req-3", "build calculator")
            store.append_round("req-3", "frontend?", "react")
            store.save_final_plan("req-3", {"summary": "ok"})

            latest = store.load_context("req-3", "build calculator")
            self.assertEqual(len(latest["rounds"]), 1)
            self.assertEqual(latest["final_plan"], {"summary": "ok"})


if __name__ == "__main__":
    unittest.main()
