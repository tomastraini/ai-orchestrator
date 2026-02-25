from __future__ import annotations

import tempfile
import unittest
import os

from services.workspace.project_index import scan_projects_root


class StackDetectionTests(unittest.TestCase):
    def test_detects_python_and_dotnet_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects_root = os.path.join(tmp, "projects")
            os.makedirs(projects_root, exist_ok=True)

            py_proj = os.path.join(projects_root, "pyapp")
            os.makedirs(py_proj, exist_ok=True)
            with open(os.path.join(py_proj, "pyproject.toml"), "w", encoding="utf-8") as fh:
                fh.write("[project]\nname='pyapp'\n")

            dn_proj = os.path.join(projects_root, "dotnetapp")
            os.makedirs(dn_proj, exist_ok=True)
            with open(os.path.join(dn_proj, "dotnetapp.csproj"), "w", encoding="utf-8") as fh:
                fh.write("<Project></Project>")

            scanned = scan_projects_root(projects_root)
            by_name = {x["name"]: x for x in scanned["projects"]}
            self.assertIn("python", by_name["pyapp"]["stacks"])
            self.assertIn("dotnet", by_name["dotnetapp"]["stacks"])


if __name__ == "__main__":
    unittest.main()

