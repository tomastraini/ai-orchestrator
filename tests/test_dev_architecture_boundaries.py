from __future__ import annotations

import ast
from pathlib import Path
import unittest


class DevArchitectureBoundariesTests(unittest.TestCase):
    def test_services_dev_python_files_are_within_500_lines(self) -> None:
        root = Path("services/dev")
        oversize: list[str] = []
        for path in sorted(root.rglob("*.py")):
            with path.open("r", encoding="utf-8") as fh:
                line_count = sum(1 for _ in fh)
            if line_count > 500:
                oversize.append(f"{path.as_posix()}:{line_count}")
        self.assertEqual(oversize, [], msg=f"Oversize files detected: {oversize}")

    def test_dev_modules_do_not_import_pm_layer(self) -> None:
        root = Path("services/dev")
        violations: list[str] = []
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path.as_posix())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = str(alias.name or "")
                        if name == "services.pm" or name.startswith("services.pm."):
                            violations.append(f"{path.as_posix()} imports {name}")
                if isinstance(node, ast.ImportFrom):
                    module = str(node.module or "")
                    if module == "services.pm" or module.startswith("services.pm."):
                        violations.append(f"{path.as_posix()} imports from {module}")
        self.assertEqual(violations, [], msg=f"Dev->PM coupling violations: {violations}")


if __name__ == "__main__":
    unittest.main()
