from __future__ import annotations

import os
import unittest

os.environ.setdefault("AZURE_OPENAI_KEY", "test-key")


class ServiceImportCompatibilityTests(unittest.TestCase):
    def test_flat_pm_imports_still_work(self) -> None:
        from services.pm_service import PMServiceError, create_plan  # noqa: F401
        from services.pm_context_store import PMContextStore  # noqa: F401

    def test_flat_dev_imports_still_work(self) -> None:
        from services.dev_service import DevService  # noqa: F401
        from services.dev_master_graph import DevMasterGraph  # noqa: F401
        from services.dev_executor import execute_dev_tasks  # noqa: F401

    def test_domain_package_imports_work(self) -> None:
        from services.pm.pm_service import PMServiceError, create_plan  # noqa: F401
        from services.dev.dev_service import DevService  # noqa: F401
        from services.pr.pr_service import PRService  # noqa: F401


if __name__ == "__main__":
    unittest.main()
