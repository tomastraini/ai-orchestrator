from __future__ import annotations

import unittest

from services.dev.intent_router import INTENT_ANALYSIS, INTENT_CODE, INTENT_EXECUTION, route_plan_intent


class IntentRouterTests(unittest.TestCase):
    def test_code_modification_when_targets_present(self) -> None:
        intent = route_plan_intent({"summary": "update app", "target_files": [{"file_name": "App.tsx"}]})
        self.assertEqual(intent, INTENT_CODE)

    def test_execution_intent_when_bootstrap_only(self) -> None:
        intent = route_plan_intent({"summary": "run setup", "bootstrap_commands": [{"command": "npm i"}], "target_files": []})
        self.assertEqual(intent, INTENT_EXECUTION)

    def test_analysis_intent_from_summary_marker(self) -> None:
        intent = route_plan_intent({"summary": "explain current architecture tradeoff", "target_files": [], "bootstrap_commands": []})
        self.assertEqual(intent, INTENT_ANALYSIS)


if __name__ == "__main__":
    unittest.main()

