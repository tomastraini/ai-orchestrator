from __future__ import annotations

import os
from typing import Any, Dict, List

from shared.pathing import normalize_rel_path


CONFIG_FILES = (
    "package.json",
    "tsconfig.json",
    "jsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    "webpack.config.js",
    "next.config.js",
    "pyproject.toml",
    "requirements.txt",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Cargo.toml",
    "go.mod",
    "*.csproj",
    "*.sln",
    "*.xcodeproj",
    "AndroidManifest.xml",
)


def _matches_pattern(name: str, pattern: str) -> bool:
    if not pattern.startswith("*."):
        return name == pattern
    return name.endswith(pattern[1:])


def detect_configs_and_toolchain(active_root: str, rel_files: List[str]) -> Dict[str, Any]:
    configs: List[str] = []
    for rel in rel_files:
        base = os.path.basename(rel)
        if any(_matches_pattern(base, pattern) for pattern in CONFIG_FILES):
            configs.append(normalize_rel_path(rel))
    configs = sorted(set(configs))

    stacks: List[Dict[str, Any]] = []
    config_set = set(configs)
    if any(x.endswith("package.json") for x in config_set):
        stacks.append({"name": "node", "confidence": 0.9})
    if any(x.endswith("pyproject.toml") or x.endswith("requirements.txt") for x in config_set):
        stacks.append({"name": "python", "confidence": 0.85})
    if any(x.endswith("pom.xml") or x.endswith("build.gradle") or x.endswith("build.gradle.kts") for x in config_set):
        stacks.append({"name": "jvm", "confidence": 0.85})
    if any(x.endswith(".csproj") or x.endswith(".sln") for x in config_set):
        stacks.append({"name": "dotnet", "confidence": 0.85})
    if any(x.endswith("Cargo.toml") for x in config_set):
        stacks.append({"name": "rust", "confidence": 0.85})
    if any(x.endswith("go.mod") for x in config_set):
        stacks.append({"name": "go", "confidence": 0.85})
    if any("AndroidManifest.xml" in x for x in config_set):
        stacks.append({"name": "android", "confidence": 0.9})
    if any(x.endswith(".xcodeproj") for x in config_set):
        stacks.append({"name": "ios", "confidence": 0.9})
    return {"configs": configs, "toolchain": stacks}
