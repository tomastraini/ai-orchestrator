from __future__ import annotations

import os
import tempfile
import unittest

from services.workspace.project_index import build_cognition_index


class CognitionLongTailTests(unittest.TestCase):
    def test_detects_android_and_ios_signals_without_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "app", "src", "main"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "ios", "Runner.xcodeproj"), exist_ok=True)
            with open(os.path.join(tmp, "app", "src", "main", "AndroidManifest.xml"), "w", encoding="utf-8") as fh:
                fh.write("<manifest />\n")
            with open(os.path.join(tmp, "app", "src", "main", "MainActivity.kt"), "w", encoding="utf-8") as fh:
                fh.write("class MainActivity\n")
            with open(os.path.join(tmp, "ios", "AppDelegate.swift"), "w", encoding="utf-8") as fh:
                fh.write("class AppDelegate {}\n")
            cognition = build_cognition_index(
                tmp,
                [
                    "app/src/main/AndroidManifest.xml",
                    "app/src/main/MainActivity.kt",
                    "ios/Runner.xcodeproj/project.pbxproj",
                    "ios/AppDelegate.swift",
                ],
            )
        toolchain = cognition.get("toolchain", [])
        names = {str(item.get("name", "")) for item in toolchain if isinstance(item, dict)}
        self.assertTrue({"android", "ios"}.intersection(names), msg=str(toolchain))
        self.assertTrue(bool(cognition.get("resolution_hints", {}).get("ai_ranked_candidates", [])))

    def test_detects_java_csharp_cpp_and_cobol_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
            fixtures = {
                "src/Main.java": "class Main { public static void main(String[] args) {} }",
                "src/Program.cs": "public class Program { static void Main() {} }",
                "src/main.cpp": "int main(){return 0;}",
                "src/legacy.cbl": "IDENTIFICATION DIVISION.",
            }
            for rel, content in fixtures.items():
                abs_path = os.path.join(tmp, rel.replace("/", os.sep))
                with open(abs_path, "w", encoding="utf-8") as fh:
                    fh.write(content)
            cognition = build_cognition_index(tmp, list(fixtures.keys()))
        file_rows = cognition.get("files", [])
        languages = {str(row.get("language", "")) for row in file_rows if isinstance(row, dict)}
        self.assertIn("jvm", languages)
        self.assertIn("csharp", languages)
        self.assertIn("cpp", languages)
        self.assertIn("cobol", languages)


if __name__ == "__main__":
    unittest.main()
