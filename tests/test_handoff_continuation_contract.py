from __future__ import annotations

import unittest

from services.dev.phases.ingest_pm_plan import run as ingest_pm_plan
from services.pm.dev_handoff_store import build_dev_handoff


class HandoffContinuationContractTests(unittest.TestCase):
    def test_build_handoff_contains_continuation_defaults(self) -> None:
        handoff = build_dev_handoff(
            request_id="req-1",
            plan={
                "project_ref": {"name": "calc", "path_hint": "projects/calc"},
                "target_files": [],
                "bootstrap_commands": [],
                "target_intents": [],
                "repo_structure_snapshot": {},
                "pm_checklist": {},
                "constraints": [],
                "validation": [],
            },
            rounds=[],
        )
        continuation = handoff.get("continuation", {})
        self.assertIsInstance(continuation, dict)
        self.assertEqual(continuation.get("trigger_type"), "initial")
        self.assertEqual(continuation.get("continuation_mode"), "off")

    def test_ingest_rehydrates_continuation_fields(self) -> None:
        state = {
            "current_step": "",
            "logs": [],
            "phase_status": {"ingest_pm_plan": "pending"},
            "plan": {"project_ref": {"name": "calc", "path_hint": "projects/calc"}},
            "handoff": {
                "project_root": "projects/calc",
                "continuation": {
                    "session_id": "s-1",
                    "parent_request_id": "req-0",
                    "iteration_index": 2,
                    "continuation_reason": "improvement",
                    "delta_requirement": "add tests",
                    "prior_run_summary": "run 1 done",
                    "carry_forward_memory": True,
                    "trigger_type": "improvement",
                    "continuation_mode": "prompt",
                },
            },
        }

        class _GraphStub:
            @staticmethod
            def _emit(_state, _msg):
                return None

            @staticmethod
            def _emit_event(_state, _category, **_metadata):
                return None

        out = ingest_pm_plan(state, _GraphStub)
        self.assertEqual(out.get("session_id"), "s-1")
        self.assertEqual(out.get("iteration_index"), 2)
        self.assertEqual(out.get("continuation_mode"), "prompt")
        self.assertEqual(out.get("delta_requirement"), "add tests")


if __name__ == "__main__":
    unittest.main()
