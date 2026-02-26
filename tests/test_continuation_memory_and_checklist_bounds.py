from __future__ import annotations

import unittest

from services.dev.dev_master_graph import DevMasterGraph


class ContinuationMemoryAndChecklistBoundsTests(unittest.TestCase):
    def test_memory_buckets_are_bounded(self) -> None:
        memory = DevMasterGraph._default_repository_memory()
        memory["attempted_commands"] = [f"cmd-{i}" for i in range(300)]
        trimmed = DevMasterGraph._trim_repository_memory(memory)
        self.assertLessEqual(len(trimmed.get("attempted_commands", [])), DevMasterGraph.MEMORY_LIMITS["attempted_commands"])

    def test_delta_checklist_item_not_duplicated_on_rebuild(self) -> None:
        state = {
            "handoff": {
                "internal_checklist": [
                    {
                        "id": "todo_delta_1",
                        "kind": "validation",
                        "description": "validate delta requirement: add tests",
                        "status": "pending",
                    }
                ]
            },
            "bootstrap_tasks": [],
            "implementation_targets": [],
            "validation_tasks": [],
            "final_compile_tasks": [],
            "delta_requirement": "add tests",
            "iteration_index": 1,
            "internal_checklist": [],
            "checklist_index": {},
            "logs": [],
        }
        DevMasterGraph._build_internal_checklist(state)  # type: ignore[arg-type]
        delta_items = [x for x in state.get("internal_checklist", []) if x.get("id") == "todo_delta_1"]
        self.assertEqual(len(delta_items), 1)


if __name__ == "__main__":
    unittest.main()
