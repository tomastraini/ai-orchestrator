from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from services.dev.dev_master_graph import DevMasterGraph


class SetupStrategyTests(unittest.TestCase):
    def test_infers_standardized_setup_tasks_when_safe_commands_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = os.path.join(tmp, "weather")
            os.makedirs(project_dir, exist_ok=True)
            with open(os.path.join(project_dir, "package.json"), "w", encoding="utf-8") as fh:
                json.dump({"name": "weather", "scripts": {"build": "echo build"}}, fh)

            state = {
                "scope_root": tmp,
                "project_root": "projects/weather",
                "implementation_targets": [
                    {"file_name": "index.html"},
                    {"file_name": "main.js"},
                    {"file_name": "styles.css"},
                ],
                "telemetry_events": [],
                "current_step": "derive_dev_todos",
                "request_id": "req-1",
            }
            with patch.object(DevMasterGraph, "_is_validation_command_executable", return_value=(True, "")):
                tasks = DevMasterGraph._infer_bootstrap_tasks_from_intent(state)  # type: ignore[arg-type]
            self.assertTrue(tasks)
            self.assertTrue(any("install" in str(task.command or "") for task in tasks))


if __name__ == "__main__":
    unittest.main()
