from __future__ import annotations

import unittest

from services.dev.command_policy import assess_risk, normalize_non_interactive


class CommandPolicyTests(unittest.TestCase):
    def test_normalize_non_interactive_for_npx(self) -> None:
        cmd = normalize_non_interactive("npx create-react-app front-end --template typescript")
        self.assertIn("npx --yes", cmd)

    def test_assess_risk_for_force(self) -> None:
        risky, reason = assess_risk("npm audit fix --force")
        self.assertTrue(risky)
        self.assertIn("force", reason)


if __name__ == "__main__":
    unittest.main()

