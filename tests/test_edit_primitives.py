from __future__ import annotations

import os
import tempfile
import unittest

from services.dev.edit_primitives import insert_after_symbol, patch_region, rename_path, replace_symbol, update_imports


class EditPrimitivesTests(unittest.TestCase):
    def test_patch_region_replaces_content(self) -> None:
        updated, changed = patch_region("a", "b")
        self.assertTrue(changed)
        self.assertEqual(updated, "b")

    def test_replace_symbol(self) -> None:
        updated, changed = replace_symbol("hello world", "world", "team")
        self.assertTrue(changed)
        self.assertEqual(updated, "hello team")

    def test_insert_after_symbol(self) -> None:
        updated, changed = insert_after_symbol("abc", "a", "Z")
        self.assertTrue(changed)
        self.assertEqual(updated, "aZbc")

    def test_update_imports_replaces_existing(self) -> None:
        original = "import x from 'old'\nconsole.log(x)\n"
        updated, changed = update_imports(original, "old", "import y from 'old'")
        self.assertTrue(changed)
        self.assertIn("import y from 'old'", updated)

    def test_update_imports_prepends_missing(self) -> None:
        original = "print('ok')\n"
        updated, changed = update_imports(original, "x", "import x")
        self.assertTrue(changed)
        self.assertTrue(updated.startswith("import x"))

    def test_rename_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "main.ts")
            dst = os.path.join(tmp, "main.tsx")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("content")
            changed, note = rename_path(src_path=src, dest_path=dst)
            self.assertTrue(changed)
            self.assertEqual(note, "renamed")
            self.assertFalse(os.path.exists(src))
            self.assertTrue(os.path.exists(dst))


if __name__ == "__main__":
    unittest.main()
