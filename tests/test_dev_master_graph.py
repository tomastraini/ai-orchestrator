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
                    "expected_path_hint": "projects/calc",
                    "modification_type": "create",
                    "details": "note",
                }
            ],
            "constraints": ["Do not push"],
            "validation": ["Build passes"],
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


if __name__ == "__main__":
    unittest.main()
