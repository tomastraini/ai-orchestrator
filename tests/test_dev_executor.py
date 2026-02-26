from __future__ import annotations

import tempfile
import unittest

from services.dev.dev_executor import (
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
            logs, touched, errors, _, _, _ = execute_dev_tasks(tasks, scope_root=tmp)
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
            _, _, errors, _, _, _ = execute_dev_tasks(tasks, scope_root=tmp)
            self.assertTrue(any("[BLOCKED]" in e for e in errors), msg=str(errors))

    def test_deterministic_rewrite_for_interactive_prompt_adds_yes_flag(self) -> None:
        rewritten = rewrite_command_deterministic(
            "tool create sample-app",
            "interactive_prompt",
        )
        self.assertEqual(rewritten, "tool create sample-app --yes")

    def test_deterministic_rewrite_normalizes_vite_target(self) -> None:
        vite = rewrite_command_deterministic(
            "npm create vite@latest projects/react-calculator -- --template react-ts",
            "path_issue",
        )
        self.assertIn("npm create vite@latest .", vite)

    def test_deterministic_rewrite_normalizes_vite_target_for_npm_init(self) -> None:
        vite = rewrite_command_deterministic(
            "npm init vite@latest projects/react-calculator -- --template react",
            "path_issue",
        )
        self.assertIn("npm init vite@latest .", vite)

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
            logs, _, errors, attempts, pending, outcomes = execute_dev_tasks(
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
            self.assertTrue(any(str(x.get("status")) == "failed" for x in outcomes), msg=str(outcomes))

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
            logs, _, errors, attempts, pending, outcomes = execute_dev_tasks(
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
            self.assertTrue(any(str(x.get("status")) == "pending_llm" for x in outcomes), msg=str(outcomes))

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
            _, touched, errors, _, _, _ = execute_dev_tasks(tasks, scope_root=tmp)
            self.assertEqual(errors, [], msg=str(errors))
            self.assertTrue(any("calculator" in path for path in touched), msg=str(touched))
            self.assertFalse(any("projects/calculator/projects/calculator" in p.replace("\\", "/") for p in touched))

    def test_constraints_block_dev_server_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t5b",
                    description="blocked by constraint",
                    command="npm start",
                    cwd=".",
                )
            ]
            _, _, errors, _, _, _ = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                constraints=["Do not run dev server in bootstrap"],
            )
            self.assertTrue(any("violates constraint" in e for e in errors), msg=str(errors))

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
            logs, _, errors, _, _, _ = execute_dev_tasks(
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
            _, _, errors, _, _, _ = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                log_sink=captured.append,
                heartbeat_seconds=0.1,
            )
            self.assertEqual(errors, [], msg=str(errors))
            self.assertTrue(any("[HEARTBEAT]" in line for line in captured), msg=str(captured))

    def test_runtime_prompt_callback_forwards_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            captured: list[str] = []
            prompts: list[str] = []
            tasks = [
                DevTask(
                    id="t8",
                    description="interactive prompt handled",
                    command="python -c \"print('Ok to proceed? (y/N)'); x=input(); print('answer=' + x)\"",
                    cwd=".",
                )
            ]
            logs, _, errors, _, _, outcomes = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                log_sink=captured.append,
                ask_runtime_prompt=lambda q: (prompts.append(q) or "y"),
                heartbeat_seconds=0.1,
            )
            self.assertEqual(errors, [], msg=str(errors))
            self.assertTrue(any("[INTERACTIVE_PROMPT]" in line for line in logs), msg=str(logs))
            self.assertTrue(any("forwarded response='y'" in line for line in logs), msg=str(logs))
            self.assertTrue(any("Ok to proceed?" in q for q in prompts), msg=str(prompts))
            self.assertTrue(any(str(x.get("status")) == "completed" for x in outcomes), msg=str(outcomes))

    def test_service_smoke_mode_terminates_after_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t9",
                    description="long running simulated server",
                    command="python -c \"import time; print('localhost:5173'); time.sleep(2)\"",
                    cwd=".",
                )
            ]
            _, _, errors, _, _, outcomes = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                command_run_mode="service_smoke",
                timeout_seconds=5,
                heartbeat_seconds=0.1,
            )
            self.assertEqual(errors, [], msg=str(errors))
            self.assertTrue(any(bool(x.get("evidence", {}).get("smoke_ready")) for x in outcomes), msg=str(outcomes))

    def test_auto_mode_promotes_dev_server_to_service_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t10",
                    description="auto mode should detect long-running dev server",
                    command="python -c \"import time; print(' npm run dev '); print('VITE v7.3.1 ready in 20 ms'); time.sleep(2)\"",
                    cwd=".",
                )
            ]
            _, _, errors, _, _, outcomes = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                command_run_mode="auto",
                timeout_seconds=5,
                heartbeat_seconds=0.1,
            )
            self.assertEqual(errors, [], msg=str(errors))
            self.assertEqual(len(outcomes), 1)
            self.assertEqual(outcomes[0].get("run_mode"), "service_smoke")
            self.assertTrue(bool(outcomes[0].get("evidence", {}).get("smoke_ready")), msg=str(outcomes))

    def test_emits_structured_events_to_event_sink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events: list[dict] = []
            tasks = [
                DevTask(
                    id="t11",
                    description="event sink coverage",
                    command="python -c \"print('ok')\"",
                    cwd=".",
                )
            ]
            _, _, errors, _, _, _ = execute_dev_tasks(
                tasks,
                scope_root=tmp,
                event_sink=events.append,
            )
            self.assertEqual(errors, [], msg=str(errors))
            categories = {str(event.get("category", "")) for event in events}
            self.assertIn("command_provenance", categories)
            self.assertIn("run_attempt", categories)
            self.assertIn("task_outcome", categories)


if __name__ == "__main__":
    unittest.main()
