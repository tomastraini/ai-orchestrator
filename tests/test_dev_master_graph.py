from __future__ import annotations

import os
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
        self.assertIn("[PHASE_START] execute_final_compile_gate", logs_blob)
        self.assertIn("[PASS_SUMMARY]", logs_blob)
        self.assertIn("[IMPLEMENTATION]", logs_blob)
        self.assertIn("[CHECKLIST]", logs_blob)
        self.assertIn("[FINAL]", logs_blob)
        self.assertEqual(state.get("final_compile_status"), "completed")
        self.assertTrue(
            all(str(item.get("status", "")) == "completed" for item in state.get("internal_checklist", [])),
            msg=str(state.get("internal_checklist", [])),
        )

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

    def test_bootstrap_dev_server_in_auto_mode_does_not_block_execution(self) -> None:
        plan = self._sample_plan()
        plan["bootstrap_commands"] = [
            {
                "cwd": ".",
                "command": "python -c \"import time; print(' npm run dev '); print('VITE v7.3.1 ready in 20 ms'); print('Local: http://localhost:5173/'); time.sleep(2)\"",
                "purpose": "start dev server",
            }
        ]
        graph = DevMasterGraph()
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-bootstrap-dev-server-auto-1",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
            )
        self.assertEqual(state["status"], "completed", msg=str(state.get("errors", [])))
        bootstrap_outcomes = [
            outcome
            for outcome in state.get("task_outcomes", [])
            if str(outcome.get("task_id", "")).startswith("bootstrap_")
        ]
        self.assertTrue(bootstrap_outcomes, msg=str(state.get("task_outcomes", [])))
        self.assertTrue(
            any(
                str(outcome.get("run_mode", "")) == "service_smoke"
                and bool(outcome.get("evidence", {}).get("smoke_ready"))
                for outcome in bootstrap_outcomes
            ),
            msg=str(bootstrap_outcomes),
        )
        logs_blob = "\n".join(state.get("logs", []))
        self.assertIn("[PHASE] implementation", logs_blob)

    def test_validation_skips_when_pm_requirement_is_non_executable(self) -> None:
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
        self.assertEqual(state.get("validation_status"), "skipped")
        self.assertEqual(state.get("phase_status", {}).get("execute_validation_phase"), "skipped")
        self.assertTrue(any("none were executable" in line.lower() for line in state.get("logs", [])))

    def test_react_component_targets_apply_generic_updates(self) -> None:
        graph = DevMasterGraph()
        plan = {
            "summary": "Create calculator",
            "project_mode": "new_project",
            "project_ref": {"name": "calc", "path_hint": "projects/calc"},
            "stack": {"frontend": "React", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "new_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [
                {
                    "file_name": "Calculator.tsx",
                    "expected_path_hint": "projects/calc/src/Calculator.tsx",
                    "modification_type": "add",
                    "details": "Create calculator component with add and subtract",
                },
                {
                    "file_name": "App.tsx",
                    "expected_path_hint": "projects/calc/src/App.tsx",
                    "modification_type": "modify",
                    "details": "Import and render calculator component",
                },
            ],
            "constraints": ["none"],
            "validation": ["python -c \"print('ok')\""],
            "clarification_summary": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "calc", "src"), exist_ok=True)
            with open(os.path.join(tmp, "calc", "src", "App.tsx"), "w", encoding="utf-8") as fh:
                fh.write("export default function App(){return <div/>}\n")
            state = graph.run(request_id="calc-wire-1", plan=plan, scope_root=tmp, ask_user=lambda _: "n/a")
            with open(os.path.join(tmp, "calc", "src", "App.tsx"), "r", encoding="utf-8") as fh:
                app_content = fh.read()
        self.assertEqual(state.get("status"), "completed", msg=str(state.get("errors", [])))
        self.assertTrue(len(app_content.strip()) > 0)
        self.assertNotIn("IMPLEMENT PASS", app_content)

    def test_telemetry_events_are_emitted(self) -> None:
        graph = DevMasterGraph()
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="telemetry-1",
                plan=self._sample_plan(),
                scope_root=tmp,
                ask_user=lambda _: "n/a",
            )
        categories = [str(event.get("category", "")) for event in state.get("telemetry_events", []) if isinstance(event, dict)]
        self.assertIn("plan_ingest", categories)
        self.assertIn("final_summary", categories)

    def test_final_compile_gate_blocks_completion_when_missing(self) -> None:
        plan = self._sample_plan()
        plan["bootstrap_commands"] = []
        plan["validation"] = []
        plan["stack"] = {"frontend": "Generic", "backend": None, "language_preferences": ["TypeScript"]}
        graph = DevMasterGraph()
        with tempfile.TemporaryDirectory() as tmp:
            state = graph.run(
                request_id="req-final-compile-missing-1",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
            )
        self.assertEqual(state.get("final_compile_status"), "failed")
        self.assertEqual(state.get("status"), "implementation_failed")
        self.assertTrue(
            any("no terminating compile/build command inferred" in err.lower() for err in state.get("errors", [])),
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

    def test_resolve_target_file_path_uses_file_leaf_for_directory_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            active_root = f"{tmp}/projects/calc"
            resolved = DevMasterGraph._resolve_target_file_path(
                scope_root=tmp,
                project_root="projects/calc",
                active_project_root=active_root,
                expected_path_hint="projects/calc/src/components",
                file_name="src/components/Calculator.tsx",
            )
            self.assertEqual(
                resolved.replace("\\", "/"),
                f"{active_root}/src/components/Calculator.tsx".replace("\\", "/"),
            )

    def test_implementation_recovers_when_target_is_directory(self) -> None:
        graph = DevMasterGraph()
        plan = {
            "summary": "Recover implementation target",
            "project_mode": "new_project",
            "project_ref": {"name": "calc", "path_hint": "projects/calc"},
            "stack": {"frontend": "Generic", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "new_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [
                {
                    "file_name": "Calculator.tsx",
                    "expected_path_hint": "projects/calc/src/components",
                    "modification_type": "create",
                    "details": "create calculator component",
                }
            ],
            "constraints": ["none"],
            "validation": ["python -c \"print('ok')\""],
            "clarification_summary": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "calc", "src", "components", "Calculator.tsx"), exist_ok=True)
            state = graph.run(
                request_id="impl-recover-dir-target-1",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
            )
        self.assertEqual(state["status"], "completed", msg=str(state.get("errors", [])))
        self.assertTrue(
            any("[IMPLEMENTATION_RECOVERY]" in line for line in state.get("logs", [])),
            msg=str(state.get("logs", [])),
        )

    def test_root_resolution_prefers_nested_marker_based_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outer = os.path.join(tmp, "calculator-react")
            inner = os.path.join(outer, "calculator-react")
            os.makedirs(os.path.join(inner, "src"), exist_ok=True)
            with open(os.path.join(inner, "package.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")

            state = {
                "scope_root": tmp,
                "project_root": "projects/calculator-react",
                "project_name": "calculator-react",
                "active_project_root": outer,
                "implementation_targets": [
                    {
                        "file_name": "src/App.jsx",
                        "expected_path_hint": "projects/calculator-react/src/App.jsx",
                    }
                ],
                "touched_paths": [outer, inner],
            }
            evidence = DevMasterGraph._resolve_active_project_root_after_bootstrap(
                state=state,  # type: ignore[arg-type]
                attempt_history=[
                    {
                        "stdout": f"Scaffolding project in {inner}...",
                        "stderr": "",
                        "cwd": outer,
                    }
                ],
            )
            self.assertEqual(
                str(evidence.get("selected_root", "")).replace("\\", "/"),
                inner.replace("\\", "/"),
            )
            self.assertGreaterEqual(int(evidence.get("confidence", 0)), 45)


if __name__ == "__main__":
    unittest.main()
