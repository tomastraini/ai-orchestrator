from __future__ import annotations

import os
import tempfile
import unittest

from services.workspace.project_index import build_cognition_index


class CognitionIndexTests(unittest.TestCase):
    def test_builds_symbol_index_and_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "pkg"), exist_ok=True)
            main_path = os.path.join(tmp, "main.py")
            with open(main_path, "w", encoding="utf-8") as fh:
                fh.write("def main():\n    return 1\n")
            with open(os.path.join(tmp, "pkg", "helper.py"), "w", encoding="utf-8") as fh:
                fh.write("class Helper:\n    pass\n")
            cognition = build_cognition_index(tmp, ["main.py", "pkg/helper.py"])
        self.assertEqual(cognition.get("version"), "1.0")
        self.assertGreaterEqual(int(cognition.get("file_count", 0)), 2)
        symbols = cognition.get("symbol_index", {}).get("by_name", {})
        self.assertIn("main", symbols)
        entrypoints = cognition.get("entrypoints", [])
        self.assertTrue(any(str(item.get("path", "")).endswith("main.py") for item in entrypoints), msg=str(entrypoints))


if __name__ == "__main__":
    unittest.main()

