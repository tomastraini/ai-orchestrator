from __future__ import annotations

import unittest

from services.dev.validation_strategy import infer_validation_strategy


class ValidationStrategyTests(unittest.TestCase):
    def test_requires_clarification_when_validation_is_non_executable(self) -> None:
        strategy = infer_validation_strategy(
            raw_validation_requirements=["verify UI behavior"],
            validation_commands=[],
            unresolved_validation_requirements=[
                {"requirement": "verify UI behavior", "reason": "non_executable_requirement"}
            ],
            browser_adapter_available=False,
        )
        self.assertTrue(bool(strategy.get("requires_clarification")))
        self.assertEqual(strategy.get("mode"), "manual_or_browser_clarification")
        self.assertTrue(isinstance(strategy.get("followup_options"), list))

    def test_uses_executable_mode_when_commands_exist(self) -> None:
        strategy = infer_validation_strategy(
            raw_validation_requirements=["run tests"],
            validation_commands=["python -m pytest"],
            unresolved_validation_requirements=[],
            browser_adapter_available=False,
        )
        self.assertFalse(bool(strategy.get("requires_clarification")))
        self.assertEqual(strategy.get("mode"), "executable")


if __name__ == "__main__":
    unittest.main()
