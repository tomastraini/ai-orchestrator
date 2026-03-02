from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class OrchestratorFlowTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "AZURE_OPENAI_KEY": "dummy",
            "AZURE_OPENAI_ENDPOINT": "https://example.invalid",
            "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
        },
        clear=False,
    )
    def test_plan_mode_builds_plan_without_execution(self) -> None:
        import importlib

        orch = importlib.import_module("orchestrator")
        with (
            patch.object(orch, "create_plan", return_value={"summary": "plan"}),
            patch.object(orch, "_ask_approval", return_value=True),
            patch.object(orch, "ClaudeCodeCLIExecutor") as exec_cls_mock,
        ):
            rc = orch.run("build a todo app", mode="plan", from_latest=False)
            self.assertEqual(rc, 0)
            exec_cls_mock.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "AZURE_OPENAI_KEY": "dummy",
            "AZURE_OPENAI_ENDPOINT": "https://example.invalid",
            "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
        },
        clear=False,
    )
    def test_execute_mode_uses_latest_plan_with_claude_executor(self) -> None:
        import importlib

        orch = importlib.import_module("orchestrator")
        with (
            patch.object(orch, "_ask_approval", return_value=True),
            patch.object(orch, "_load_latest_plan", return_value=({"summary": "base"}, "req-0")),
            patch.object(orch, "ClaudeCodeCLIExecutor") as exec_cls_mock,
        ):
            exec_cls_mock.return_value.execute_plan.return_value = {
                "status": "completed",
                "build_logs": "ok",
            }
            rc = orch.run("", mode="execute", from_latest=True)
            self.assertEqual(rc, 0)
            exec_cls_mock.return_value.execute_plan.assert_called_once()

    @patch.dict(
        os.environ,
        {
            "AZURE_OPENAI_KEY": "dummy",
            "AZURE_OPENAI_ENDPOINT": "https://example.invalid",
            "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
        },
        clear=False,
    )
    def test_execute_mode_fails_when_no_latest_plan(self) -> None:
        import importlib

        orch = importlib.import_module("orchestrator")
        with patch.object(orch, "_load_latest_plan", return_value=(None, None)):
            rc = orch.run("", mode="execute", from_latest=True)
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

