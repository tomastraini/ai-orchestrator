from __future__ import annotations

import json
import os
import tempfile
import unittest

from services.dev.dev_master_graph import DevMasterGraph


class TerminalFailureGateTests(unittest.TestCase):
    def _base_plan(self) -> dict:
        return {
            "summary": "Validate graceful degradation on missing capability.",
            "project_mode": "new_project",
            "project_ref": {"name": "cap-gap", "path_hint": "projects/cap-gap"},
            "stack": {"frontend": "Node", "backend": None, "language_preferences": ["TypeScript"]},
            "pm_checklist": {
                "project_scope": "new_project",
                "architecture": "frontend_only",
                "backend_required": "no",
                "database_required": "no",
            },
            "bootstrap_commands": [],
            "target_files": [
                {
                    "file_name": "package.json",
                    "expected_path_hint": "projects/cap-gap/package.json",
                    "modification_type": "verify",
                    "details": "package must exist",
                    "creation_policy": "must_exist",
                }
            ],
            "constraints": [],
            "validation": [],
            "clarification_summary": [],
        }

    def test_missing_toolchain_degrades_without_implementation_failed(self) -> None:
        graph = DevMasterGraph()
        plan = self._base_plan()
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = os.path.join(tmp, "cap-gap")
            os.makedirs(project_dir, exist_ok=True)
            with open(os.path.join(project_dir, "package.json"), "w", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "name": "cap-gap",
                            "version": "1.0.0",
                            "scripts": {"build": "nest build"},
                        }
                    )
                )
            state = graph.run(
                request_id="terminal-gate-capability-1",
                plan=plan,
                scope_root=tmp,
                ask_user=lambda _: "n/a",
            )
        self.assertEqual(state.get("final_compile_status"), "failed")
        self.assertNotEqual(state.get("status"), "implementation_failed", msg=str(state.get("errors", [])))
        events = [event for event in state.get("telemetry_events", []) if isinstance(event, dict)]
        self.assertTrue(
            any(str(event.get("category", "")) == "deterministic_failure_signatures" for event in events),
            msg=str(events),
        )

    def test_terminal_gate_does_not_approve_no_progress_alone(self) -> None:
        gate = DevMasterGraph._terminal_failure_gate(
            {
                "errors": ["compile failed repeatedly"],
                "attempt_history": [
                    {"task_id": "t1", "category": "unknown", "stderr": "same error"},
                    {"task_id": "t1", "category": "unknown", "stderr": "same error"},
                    {"task_id": "t1", "category": "unknown", "stderr": "same error"},
                ],
            }
        )
        self.assertFalse(bool(gate.get("approved")), msg=str(gate))

    def test_finalize_marks_followup_ready_for_non_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = {
                "scope_root": tmp,
                "request_id": "req-followup",
                "current_step": "",
                "logs": [],
                "errors": [],
                "task_outcomes": [],
                "touched_paths": [],
                "attempt_history": [],
                "phase_status": {"finalize_result": "pending"},
                "bootstrap_status": "completed",
                "implementation_status": "completed",
                "validation_status": "completed",
                "final_compile_status": "failed",
                "internal_checklist": [],
                "telemetry_events": [],
            }
            out = DevMasterGraph._finalize_result_impl(state)  # type: ignore[arg-type]
            self.assertEqual(out.get("status"), "partial_progress")
            self.assertTrue(bool(out.get("ready_for_followup")))
            self.assertTrue(bool(out.get("continuation_eligible")))
            guidance = out.get("continuation_guidance", {})
            self.assertIsInstance(guidance, dict)
            self.assertIn("recommended_next_step", guidance)

    def test_finalize_blocks_followup_on_implementation_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = {
                "scope_root": tmp,
                "request_id": "req-terminal",
                "current_step": "",
                "logs": [],
                "errors": ["policy violation"],
                "task_outcomes": [],
                "touched_paths": [],
                "attempt_history": [],
                "phase_status": {"finalize_result": "pending"},
                "bootstrap_status": "completed",
                "implementation_status": "completed",
                "validation_status": "completed",
                "final_compile_status": "failed",
                "internal_checklist": [],
                "telemetry_events": [],
            }
            out = DevMasterGraph._finalize_result_impl(state)  # type: ignore[arg-type]
            self.assertEqual(out.get("status"), "implementation_failed")
            self.assertFalse(bool(out.get("continuation_eligible")))

    def test_final_compile_missing_inference_sets_validation_clarification_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = {
                "scope_root": tmp,
                "request_id": "req-compile-missing",
                "current_step": "",
                "logs": [],
                "errors": [],
                "task_outcomes": [],
                "touched_paths": [],
                "attempt_history": [],
                "phase_status": {"execute_final_compile_gate": "pending"},
                "bootstrap_status": "completed",
                "implementation_status": "completed",
                "validation_status": "completed",
                "final_compile_tasks": [],
                "detected_stacks": ["generic"],
                "active_project_root": tmp,
                "validation_followup_options": [{"id": "manual", "label": "Manual validation"}],
                "telemetry_events": [],
            }
            out = DevMasterGraph._execute_final_compile_gate_impl(state)  # type: ignore[arg-type]
            self.assertEqual(out.get("final_compile_status"), "failed")
            self.assertTrue(bool(out.get("needs_validation_clarification")))
            events = [event for event in out.get("telemetry_events", []) if isinstance(event, dict)]
            self.assertTrue(any(str(event.get("category", "")) == "compile_inference_missing" for event in events))


if __name__ == "__main__":
    unittest.main()
