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
                return_value=_FakeProcess("line one\nline two\n", returncode=0),
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


if __name__ == "__main__":
    unittest.main()

