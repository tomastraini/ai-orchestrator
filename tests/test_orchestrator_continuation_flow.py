from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class OrchestratorContinuationFlowTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "DEV_CONTINUATION_LOOP_ENABLED": "true",
            "AZURE_OPENAI_KEY": "dummy",
            "AZURE_OPENAI_ENDPOINT": "https://example.invalid",
            "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
        },
        clear=False,
    )
    def test_iterative_followups_keep_running_until_declined(self) -> None:
        import importlib

        orch = importlib.import_module("orchestrator")

        with (
            patch.object(orch, "_ask_approval", return_value=True),
            patch.object(orch, "_ask_followup", side_effect=[True, False]),
            patch.object(orch, "_ask_delta_requirement", side_effect=["improve a", "improve b"]),
            patch.object(orch, "create_plan", return_value={"summary": "delta plan", "constraints": []}),
            patch.object(orch, "_load_latest_plan_and_handoff") as load_latest_mock,
            patch.object(orch, "DevService") as dev_service_cls_mock,
        ):
            base_plan = {"summary": "base", "constraints": []}
            base_handoff = {"request_id": "req-0", "project_root": "projects/calc"}
            load_latest_mock.return_value = (base_plan, base_handoff, "req-0")

            dev_instance = dev_service_cls_mock.return_value
            dev_instance.execute_plan.side_effect = [
                {
                    "status": "completed",
                    "continuation_eligible": True,
                    "final_summary": "iter 1",
                },
                {
                    "status": "partial_progress",
                    "continuation_eligible": True,
                    "final_summary": "iter 2",
                },
                {
                    "status": "completed",
                    "continuation_eligible": True,
                    "final_summary": "iter 3",
                },
            ]

            rc = orch.run(
                "",
                mode="execute",
                from_latest=True,
                continuation_mode="prompt",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(dev_instance.execute_plan.call_count, 3)

    def test_end_intent_helper(self) -> None:
        import importlib

        orch = importlib.import_module("orchestrator")
        self.assertTrue(orch._is_end_intent("done"))
        self.assertTrue(orch._is_end_intent("no more improvements"))
        self.assertFalse(orch._is_end_intent("improve error handling"))

    @patch.dict(
        os.environ,
        {
            "DEV_CONTINUATION_LOOP_ENABLED": "false",
            "AZURE_OPENAI_KEY": "dummy",
            "AZURE_OPENAI_ENDPOINT": "https://example.invalid",
            "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
        },
        clear=False,
    )
    def test_one_shot_behavior_unchanged_when_continuation_disabled(self) -> None:
        import importlib

        orch = importlib.import_module("orchestrator")
        with (
            patch.object(orch, "_ask_approval", return_value=True),
            patch.object(orch, "_load_latest_plan_and_handoff") as load_latest_mock,
            patch.object(orch, "DevService") as dev_service_cls_mock,
        ):
            base_plan = {"summary": "base", "constraints": []}
            base_handoff = {"request_id": "req-0", "project_root": "projects/calc"}
            load_latest_mock.return_value = (base_plan, base_handoff, "req-0")
            dev_instance = dev_service_cls_mock.return_value
            dev_instance.execute_plan.return_value = {
                "status": "partial_progress",
                "continuation_eligible": True,
                "final_summary": "iter 1",
            }
            rc = orch.run(
                "",
                mode="execute",
                from_latest=True,
                continuation_mode="prompt",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(dev_instance.execute_plan.call_count, 1)


if __name__ == "__main__":
    unittest.main()
