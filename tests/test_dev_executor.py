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
            self.assertTrue(any("[WHY_THIS_STEP]" in x for x in logs), msg=str(logs))

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
            logs, _, errors, attempts, pending = execute_dev_tasks(
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
            self.assertTrue(
                any("[WHY_RETRY]" in x or "[WHY_RETRY_STOPPED]" in x for x in logs),
                msg=str(logs),
            )

    def test_resolves_redundant_projects_prefix_in_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t5",
                    description="redundant projects prefix",
                    command="python -c \"print('ok')\"",
                    cwd="projects/calculator/projects/calculator",
                )
            ]
            _, touched, errors, _, _ = execute_dev_tasks(tasks, scope_root=tmp)
            self.assertEqual(errors, [], msg=str(errors))
            self.assertTrue(any("calculator" in path for path in touched), msg=str(touched))

    def test_streams_output_to_log_sink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            captured: list[str] = []
            tasks = [
                DevTask(
                    id="t6",
                    description="stream output",
                    command="python -c \"print('hello-stream')\"",
                    cwd=".",
                )
            ]
            logs, _, errors, _, _ = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                log_sink=captured.append,
                heartbeat_seconds=0.0,
            )
            self.assertEqual(errors, [], msg=str(errors))
            self.assertTrue(any("[STREAM_STDOUT]" in line for line in captured), msg=str(captured))
            self.assertTrue(any("hello-stream" in line for line in logs), msg=str(logs))

    def test_emits_heartbeat_for_quiet_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            captured: list[str] = []
            tasks = [
                DevTask(
                    id="t7",
                    description="quiet command",
                    command="python -c \"import time; time.sleep(0.6); print('done')\"",
                    cwd=".",
                )
            ]
            _, _, errors, _, _ = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                log_sink=captured.append,
                heartbeat_seconds=0.1,
            )
            self.assertEqual(errors, [], msg=str(errors))
            self.assertTrue(any("[HEARTBEAT]" in line for line in captured), msg=str(captured))


if __name__ == "__main__":
    unittest.main()
