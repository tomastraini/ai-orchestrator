from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.execution.claude_cli_executor import ClaudeCodeCLIExecutor


class _FakeProcess:
    def __init__(self, output: str, returncode: int = 0) -> None:
        self.stdout = io.StringIO(output)
        self.returncode = returncode

    def wait(self, timeout: int | None = None) -> None:
        _ = timeout

    def kill(self) -> None:
        self.returncode = 124


class ClaudeCodeCLIExecutorTests(unittest.TestCase):
    def test_execute_plan_streams_logs_and_persists_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executor = ClaudeCodeCLIExecutor(repo_root=tmp)
            logs: list[str] = []
            with patch(
                "services.execution.claude_cli_executor.subprocess.Popen",
                side_effect=[
                    _FakeProcess("preflight ok\n", returncode=0),
                    _FakeProcess("line one\nline two\n", returncode=0),
                ],
            ):
                result = executor.execute_plan(
                    {"summary": "Do work", "project_ref": {"path_hint": "projects/sample"}},
                    request_id="req-1",
                    log_sink=logs.append,
                )

            self.assertEqual(result["status"], "completed")
            self.assertIn("line one", (result.get("build_logs") or ""))
            self.assertTrue(any("line two" in item for item in logs))
            summary_path = Path(tmp) / ".orchestrator" / "runs" / "req-1" / "summary.json"
            self.assertTrue(summary_path.exists())

    def test_preflight_auth_failure_returns_actionable_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executor = ClaudeCodeCLIExecutor(repo_root=tmp)
            with patch(
                "services.execution.claude_cli_executor.subprocess.Popen",
                return_value=_FakeProcess(
                    'Failed to authenticate. API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired"}}\n',
                    returncode=1,
                ),
            ):
                result = executor.execute_plan(
                    {"summary": "Do work", "project_ref": {"path_hint": "projects/sample"}},
                    request_id="req-auth-fail",
                )
            self.assertEqual(result["status"], "implementation_failed")
            self.assertIn("preflight failed", result["final_summary"].lower())
            self.assertIn("Run `claude login`", result.get("build_logs") or "")


if __name__ == "__main__":
    unittest.main()

