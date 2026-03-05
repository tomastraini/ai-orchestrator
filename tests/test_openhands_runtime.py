from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.execution.openhands_runtime import OpenHandsRuntime


class _FakeProcess:
    def __init__(self, output: str, returncode: int = 0) -> None:
        self.stdout = io.StringIO(output)
        self.returncode = returncode

    def wait(self, timeout: int | None = None) -> None:
        _ = timeout

    def kill(self) -> None:
        self.returncode = 124


class OpenHandsRuntimeTests(unittest.TestCase):
    def test_execute_plan_runs_full_minion_loop_and_writes_ai_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = OpenHandsRuntime(repo_root=tmp)
            with patch(
                "services.execution.openhands_runtime.subprocess.Popen",
                side_effect=[
                    _FakeProcess("planner ok\n", returncode=0),
                    _FakeProcess("builder ok\n", returncode=0),
                    _FakeProcess("validator ok\n", returncode=0),
                    _FakeProcess("finalizer ok\n", returncode=0),
                ],
            ):
                result = runtime.execute_plan(
                    {"summary": "Build feature", "project_ref": {"path_hint": "projects/sample"}},
                    request_id="req-openhands-ok",
                )
            self.assertEqual(result["status"], "completed")
            self.assertTrue((Path(tmp) / ".ai" / "spec.json").exists())
            self.assertTrue((Path(tmp) / ".ai" / "plan.json").exists())
            self.assertTrue((Path(tmp) / ".ai" / "audit.json").exists())
            self.assertTrue((Path(tmp) / ".ai" / "worklog.jsonl").exists())

    def test_execute_plan_runs_specialists_after_validator_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = OpenHandsRuntime(repo_root=tmp)
            with patch(
                "services.execution.openhands_runtime.subprocess.Popen",
                side_effect=[
                    _FakeProcess("planner ok\n", returncode=0),
                    _FakeProcess("builder ok\n", returncode=0),
                    _FakeProcess("validator fail\n", returncode=1),
                    _FakeProcess("test fixer ok\n", returncode=0),
                    _FakeProcess("dependency fixer ok\n", returncode=0),
                    _FakeProcess("validator retry ok\n", returncode=0),
                    _FakeProcess("finalizer ok\n", returncode=0),
                ],
            ):
                result = runtime.execute_plan(
                    {"summary": "Build feature", "project_ref": {"path_hint": "projects/sample"}},
                    request_id="req-openhands-specialist",
                )
            self.assertEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
