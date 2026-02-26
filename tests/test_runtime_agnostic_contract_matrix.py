from __future__ import annotations

import unittest

from services.dev.dev_master_graph import DevMasterGraph


class RuntimeAgnosticContractMatrixTests(unittest.TestCase):
    def _state_for_intent(self, summary: str, frontend: str, backend: str | None, langs: list[str]) -> dict:
        return {
            "plan": {
                "summary": summary,
                "project_mode": "new_project",
                "stack": {
                    "frontend": frontend,
                    "backend": backend,
                    "language_preferences": langs,
                },
                "target_intents": [
                    {
                        "path_hint": "projects/app/src/main",
                        "file_role": "source",
                        "change_type": "create",
                        "path_priority": 1,
                        "confidence": 0.7,
                        "rationale": summary,
                    }
                ],
            }
        }

    def test_matrix_intents_do_not_force_prescribed_bootstrap(self) -> None:
        scenarios = [
            self._state_for_intent("Create a simple React frontend.", "React", None, ["TypeScript"]),
            self._state_for_intent("Create Vue frontend with .NET backend.", "Vue", ".NET", ["TypeScript", "C#"]),
            self._state_for_intent("Create a Kotlin desktop app.", "None", None, ["Kotlin"]),
            self._state_for_intent("Create Angular supermarket CRUD app.", "Angular", None, ["TypeScript"]),
            self._state_for_intent("Create Visual Basic VPN-like app.", "None", None, ["Visual Basic"]),
        ]
        for state in scenarios:
            tasks = DevMasterGraph._infer_bootstrap_tasks_from_intent(state)  # type: ignore[arg-type]
            self.assertEqual(tasks, [])

    def test_intent_purity_blocks_unrequested_framework_commands(self) -> None:
        vue_state = self._state_for_intent(
            "Build Vue UI with dotnet API backend.",
            "Vue",
            ".NET",
            ["TypeScript", "C#"],
        )
        blocked, evidence = DevMasterGraph._violates_intent_purity(
            vue_state, "npm create vite@latest app -- --template react-ts"  # type: ignore[arg-type]
        )
        self.assertTrue(blocked, msg=str(evidence))

        allowed, evidence = DevMasterGraph._violates_intent_purity(
            vue_state, "dotnet build"  # type: ignore[arg-type]
        )
        self.assertFalse(allowed, msg=str(evidence))


if __name__ == "__main__":
    unittest.main()
