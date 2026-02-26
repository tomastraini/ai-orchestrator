from __future__ import annotations

import tempfile
import unittest

from services.dev.dev_session_store import DevSessionStore


class DevSessionStoreTests(unittest.TestCase):
    def test_create_append_close_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DevSessionStore(repo_root=tmp)
            session = store.create_session(root_requirement="build x")
            session_id = str(session.get("session_id", ""))
            self.assertTrue(session_id)

            updated = store.append_run_entry(
                session_id=session_id,
                request_id="req-1",
                parent_request_id="",
                iteration_index=1,
                trigger_type="initial",
                user_delta_requirement="",
                final_status="completed",
                summary="done",
                touched_paths=["projects/a.py"],
                pending_tasks=[],
            )
            self.assertIsInstance(updated, dict)
            latest = store.get_latest_session()
            self.assertIsInstance(latest, dict)
            self.assertEqual(len(latest.get("run_chain", [])), 1)

            closed = store.close_session(session_id, reason="done")
            self.assertEqual(closed.get("status"), "closed")

    def test_missing_store_is_graceful(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DevSessionStore(repo_root=tmp)
            self.assertIsNone(store.get_latest_session())
            self.assertIsNone(store.get_session("missing"))


if __name__ == "__main__":
    unittest.main()
