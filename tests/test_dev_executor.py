from __future__ import annotations

import tempfile
import unittest

from services.dev_executor import (
    classify_failure,
    execute_dev_tasks,
    rewrite_command_deterministic,
)
from shared.dev_schemas import DevTask


class DevExecutorTests(unittest.TestCase):
    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t1",
                    description="escape scope",
                    command="python -c \"print('x')\"",
                    cwd="../outside",
                )
            ]
            logs, touched, errors, _, _ = execute_dev_tasks(tasks, scope_root=tmp)
            self.assertEqual(logs, [])
            self.assertEqual(touched, [])
            self.assertTrue(any("[SCOPE]" in e for e in errors), msg=str(errors))

    def test_blocks_git_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t2",
                    description="push should be blocked",
                    command="git push origin HEAD",
                    cwd=".",
                )
            ]
            _, _, errors, _, _ = execute_dev_tasks(tasks, scope_root=tmp)
            self.assertTrue(any("[BLOCKED]" in e for e in errors), msg=str(errors))

    def test_deterministic_rewrite_for_nest_and_cra(self) -> None:
        cra = rewrite_command_deterministic(
            "npx create-react-app front-end --template typescript",
            "interactive_prompt",
        )
        self.assertIn("--use-npm", cra)

        nest = rewrite_command_deterministic("nest new back-end", "interactive_prompt")
        self.assertIn("@nestjs/cli new back-end", nest)
        self.assertIn("--package-manager npm", nest)
        self.assertIn("--skip-git", nest)

    def test_retry_budget_exhaustion_without_llm_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t3",
                    description="always fail",
                    command="python -c \"import sys; sys.stderr.write('boom\\n'); sys.exit(1)\"",
                    cwd=".",
                )
            ]
            logs, _, errors, attempts, pending = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                max_retries=3,
                reserve_last_for_llm=False,
            )
            self.assertGreaterEqual(len(attempts), 1)
            self.assertIsNone(pending)
            self.assertTrue(any("[FAIL]" in e for e in errors), msg=str(errors))
            self.assertTrue(any("[RUN]" in x for x in logs), msg=str(logs))

    def test_pending_llm_task_returned_after_deterministic_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t4",
                    description="interactive cancel simulation",
                    command="python -c \"import sys; sys.stderr.write('npm error canceled\\nOk to proceed? (y)\\n'); sys.exit(1)\"",
                    cwd=".",
                )
            ]
            _, _, errors, attempts, pending = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                max_retries=5,
                reserve_last_for_llm=True,
            )
            self.assertEqual(errors, [])
            self.assertIsNotNone(pending)
            self.assertGreaterEqual(len(attempts), 1)
            self.assertEqual(
                classify_failure("", "npm error canceled\nOk to proceed? (y)\n", 1),
                "interactive_prompt",
            )


if __name__ == "__main__":
    unittest.main()
