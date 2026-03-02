from __future__ import annotations

import os
import unittest

os.environ.setdefault("AZURE_OPENAI_KEY", "test-key")


class ServiceImportCompatibilityTests(unittest.TestCase):
    def test_flat_pm_imports_still_work(self) -> None:
        from services.pm_service import PMServiceError, create_plan  # noqa: F401
        from services.pm_context_store import PMContextStore  # noqa: F401

    def test_domain_package_imports_work(self) -> None:
        from services.pm.pm_service import PMServiceError, create_plan  # noqa: F401
        from services.execution.claude_cli_executor import ClaudeCodeCLIExecutor  # noqa: F401


if __name__ == "__main__":
    unittest.main()
