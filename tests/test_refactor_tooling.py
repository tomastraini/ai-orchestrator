from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class RefactorToolingTests(unittest.TestCase):
    def test_architecture_guard_runs(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cmd = [sys.executable, "scripts/architecture_guard.py"]
        result = subprocess.run(cmd, cwd=str(repo_root), check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("[ARCH_GUARD]", result.stdout)

    def test_parity_runner_executes_subset(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cmd = [sys.executable, "scripts/run_parity_suite.py", "--pattern", "test_intent_router.py"]
        result = subprocess.run(cmd, cwd=str(repo_root), check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

