from __future__ import annotations

import os
import re
from typing import List

from services.workspace.project_index import detect_stack_from_markers


VALIDATION_COMMAND_PREFIXES = (
    "npm ",
    "pnpm ",
    "yarn ",
    "python ",
    "pytest",
    "dotnet ",
    "bundle ",
    "rake ",
    "make ",
    "./",
    "bash ",
    "sh ",
)


def detect_stacks_for_root(project_dir: str) -> List[str]:
    markers: List[str] = []
    for marker in ["package.json", "pyproject.toml", "requirements.txt", "Gemfile", "Cargo.toml", "go.mod", "pom.xml"]:
        if os.path.exists(os.path.join(project_dir, marker)):
            markers.append(marker)
    top_entries = []
    try:
        top_entries = os.listdir(project_dir)
    except Exception:
        top_entries = []
    if any(x.endswith(".csproj") or x.endswith(".sln") for x in top_entries):
        markers.append("*.csproj")
    stacks = detect_stack_from_markers(markers, top_entries=top_entries)
    return stacks or ["generic"]


def default_validation_commands(stacks: List[str]) -> List[str]:
    if "dotnet" in stacks:
        return ["dotnet build", "dotnet test"]
    if "python" in stacks:
        return ["python -m pytest"]
    if "ruby" in stacks:
        return ["bundle exec rake test"]
    if "node" in stacks:
        return ["npm run build"]
    return []


def is_long_running_validation_command(command: str) -> bool:
    low = f" {str(command or '').lower()} "
    hints = [
        " npm run dev ",
        " npm start ",
        " pnpm dev ",
        " yarn dev ",
        " vite ",
        " next dev ",
        " flask run ",
        " uvicorn ",
        " rails server ",
        " dotnet watch ",
    ]
    return any(token in low for token in hints)


def infer_final_compile_commands(
    *,
    project_dir: str,
    stacks: List[str],
    validation_commands: List[str],
) -> List[str]:
    compile_candidates: List[str] = []
    for command in validation_commands:
        if not is_long_running_validation_command(command):
            compile_candidates.append(command)
    if compile_candidates:
        return compile_candidates

    default_candidates = default_validation_commands(stacks)
    for command in default_candidates:
        if not is_long_running_validation_command(command):
            compile_candidates.append(command)

    package_json = os.path.join(project_dir, "package.json")
    if not compile_candidates and os.path.exists(package_json):
        compile_candidates.append("npm run build")

    return compile_candidates


def extract_validation_command(raw: str) -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    if any(val.startswith(prefix) for prefix in VALIDATION_COMMAND_PREFIXES):
        return val
    backticked = re.findall(r"`([^`]+)`", val)
    for token in backticked:
        normalized = token.strip()
        if any(normalized.startswith(prefix) for prefix in VALIDATION_COMMAND_PREFIXES):
            return normalized
    return ""

