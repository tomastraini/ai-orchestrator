from __future__ import annotations

import tempfile
import unittest

from services.dev_master_graph import DevMasterGraph


class DevMasterGraphTests(unittest.TestCase):
    def _sample_plan(self) -> dict:
        return {
            "summary": "Create calculator",
            "project_mode": "new_project",
            "project_ref": {"name": "calc", "path_hint": "projects/calc"},
            "stack": {
                "frontend": "React",
                "backend": "NestJS",
                "language_preferences": ["TypeScript"],
            },
            "bootstrap_commands": [
                {
                    "cwd": ".",
                    "command": "python -c \"print('bootstrap')\"",
                    "purpose": "sanity bootstrap",
                }
            ],
            "target_files": [
                {
                    "file_name": "README.md",
                    "expected_path_hint": "projects/calc/README.md",
                    "modification_type": "create_file",
                    "details": "calculator implementation note",
                }
            ],
            "constraints": ["Do not push"],
            "validation": ["python -c \"print('ok')\""],
            "clarification_summary": [],
        }

    def test_linear_completion_dry_run(self) -> None:
        graph = DevMasterGraph()
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-linear-1",
                plan=self._sample_plan(),
                scope_root=tmp,
                ask_user=lambda q: "n/a",
            )
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["current_step"], "finalize_result")
        logs_blob = "\n".join(state.get("logs", []))
        self.assertIn("[INGEST]", logs_blob)
        self.assertIn("[TODO]", logs_blob)
        self.assertIn("[PREPARE]", logs_blob)
        self.assertIn("[PHASE] bootstrap", logs_blob)
        self.assertIn("[PHASE] implementation", logs_blob)
        self.assertIn("[PHASE] implementation_pass_1", logs_blob)
        self.assertIn("[PHASE] implementation_pass_2", logs_blob)
        self.assertIn("[PASS_SUMMARY]", logs_blob)
        self.assertIn("[IMPLEMENTATION]", logs_blob)
        self.assertIn("[FINAL]", logs_blob)

    def test_existing_project_without_path_prompts_clarification(self) -> None:
        plan = self._sample_plan()
        plan["project_mode"] = "existing_project"
        plan["project_ref"] = {"name": "calc", "path_hint": None}

        asked: list[str] = []

        def ask_user(question: str) -> str:
            asked.append(question)
            return "projects/calc"

        graph = DevMasterGraph()
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-linear-2",
                plan=plan,
                scope_root=tmp,
                ask_user=ask_user,
            )
        self.assertEqual(state["status"], "completed")
        self.assertGreaterEqual(len(asked), 1)
        self.assertGreaterEqual(len(state.get("clarifications", [])), 1)

    def test_llm_fallback_recovers_after_deterministic_exhaustion(self) -> None:
        plan = self._sample_plan()
        plan["bootstrap_commands"] = [
            {
                "cwd": ".",
                "command": "python -c \"import sys; sys.stderr.write('npm error canceled\\n'); sys.exit(1)\"",
                "purpose": "simulate interactive failure",
            }
        ]

        def llm_corrector(_: dict) -> str:
            return "python -c \"print('recovered')\""

        graph = DevMasterGraph()
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-linear-3",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
                handoff=None,
                llm_corrector=llm_corrector,
            )
        self.assertEqual(state["status"], "completed", msg=str(state.get("errors", [])))
        self.assertGreaterEqual(state.get("retry_count", 0), 1)
        self.assertGreaterEqual(len(state.get("attempt_history", [])), 1)
        self.assertIn("[LLM_REWRITE]", "\n".join(state.get("logs", [])))

    def test_bootstrap_failed_marks_impl_skipped(self) -> None:
        plan = self._sample_plan()
        plan["bootstrap_commands"] = [
            {
                "cwd": ".",
                "command": "python -c \"import sys; sys.exit(1)\"",
                "purpose": "irrecoverable failure",
            }
        ]

        graph = DevMasterGraph()
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-linear-4",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
                handoff=None,
                llm_corrector=lambda _: "",
                max_model_calls_per_run=0,
            )
        self.assertEqual(state["status"], "bootstrap_failed")
        self.assertEqual(state.get("implementation_status"), "impl_skipped")

    def test_log_sink_receives_early_phase_events(self) -> None:
        plan = self._sample_plan()
        graph = DevMasterGraph()
        captured: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-linear-5",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
                log_sink=captured.append,
            )
        self.assertEqual(state["status"], "completed")
        self.assertTrue(any("[PHASE_START] ingest_pm_plan" in line for line in captured), msg=str(captured))
        self.assertTrue(any("[PHASE] bootstrap" in line for line in captured), msg=str(captured))

    def test_runtime_prompt_wiring_visible_in_logs(self) -> None:
        plan = self._sample_plan()
        plan["bootstrap_commands"] = [
            {
                "cwd": ".",
                "command": "python -c \"print('Proceed? (y/N)'); x=input(); print(x)\"",
                "purpose": "runtime prompt flow",
            }
        ]
        graph = DevMasterGraph()
        captured: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-runtime-prompt-1",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda q: "y" if "[DEV RUNTIME PROMPT]" in q else "n/a",
                log_sink=captured.append,
            )
        self.assertEqual(state["status"], "completed", msg=str(state.get("errors", [])))
        self.assertTrue(any("[INTERACTIVE_PROMPT]" in line for line in captured), msg=str(captured))

    def test_validation_fails_when_pm_requirement_is_non_executable(self) -> None:
        plan = self._sample_plan()
        plan["validation"] = ["TypeScript compilation yields no type errors"]
        graph = DevMasterGraph()
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-validation-unresolved-1",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
            )
        self.assertEqual(state.get("validation_status"), "failed")
        self.assertEqual(state.get("status"), "implementation_failed")
        self.assertTrue(
            any("none were executable" in err.lower() for err in state.get("errors", [])),
            msg=str(state.get("errors", [])),
        )

    def test_resolve_target_file_path_prefers_active_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            active_root = f"{tmp}/projects/calc"
            resolved = DevMasterGraph._resolve_target_file_path(
                scope_root=tmp,
                project_root="projects/calc",
                active_project_root=active_root,
                expected_path_hint="projects/calc/src/App.tsx",
                file_name="App.tsx",
            )
            self.assertEqual(resolved.replace("\\", "/"), f"{active_root}/src/App.tsx".replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
