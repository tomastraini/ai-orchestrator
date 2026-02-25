from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from shared.pathing import normalize_rel_path


PROJECT_MARKERS = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "Gemfile",
    "*.csproj",
    "*.sln",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
)

KEY_DIR_HINTS = (
    "src",
    "app",
    "front-end",
    "frontend",
    "client",
    "server",
    "back-end",
    "backend",
    "api",
)

STACK_MARKERS: Dict[str, List[str]] = {
    "node": ["package.json"],
    "python": ["pyproject.toml", "requirements.txt", "Pipfile"],
    "ruby": ["Gemfile"],
    "dotnet": [".csproj", ".sln"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
    "java": ["pom.xml", "build.gradle"],
}


def _list_top(path: str, limit: int = 30) -> List[str]:
    if not os.path.isdir(path):
        return []
    out: List[str] = []
    for entry in sorted(os.listdir(path)):
        if entry.startswith("."):
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def _collect_markers(project_path: str) -> List[str]:
    markers: List[str] = []
    top_entries = _list_top(project_path, limit=300)
    for marker in PROJECT_MARKERS:
        if marker.startswith("*."):
            suffix = marker[1:]
            if any(entry.endswith(suffix) for entry in top_entries):
                markers.append(marker)
            continue
        if os.path.exists(os.path.join(project_path, marker)):
            markers.append(marker)
    return markers


def _collect_key_dirs(project_path: str) -> List[str]:
    key_dirs: List[str] = []
    for hint in KEY_DIR_HINTS:
        if os.path.isdir(os.path.join(project_path, hint)):
            key_dirs.append(hint)
    return key_dirs


def detect_stack_from_markers(markers: List[str], top_entries: Optional[List[str]] = None) -> List[str]:
    entries = top_entries or []
    detected: List[str] = []
    for stack, stack_markers in STACK_MARKERS.items():
        for marker in stack_markers:
            if marker in markers:
                detected.append(stack)
                break
            if marker.startswith(".") and any(x.endswith(marker) for x in entries):
                detected.append(stack)
                break
    return sorted(set(detected))


def scan_projects_root(projects_root: str) -> Dict[str, Any]:
    projects: List[Dict[str, Any]] = []
    if not os.path.isdir(projects_root):
        return {"projects_root": normalize_rel_path(projects_root), "projects": []}

    for name in sorted(os.listdir(projects_root)):
        abs_path = os.path.join(projects_root, name)
        if not os.path.isdir(abs_path) or name.startswith("."):
            continue
        top_entries = _list_top(abs_path)
        markers = _collect_markers(abs_path)
        key_dirs = _collect_key_dirs(abs_path)
        stacks = detect_stack_from_markers(markers, top_entries=top_entries)
        projects.append(
            {
                "name": name,
                "path_hint": f"projects/{name}",
                "markers": markers,
                "stacks": stacks,
                "key_dirs": key_dirs,
                "top_entries": top_entries,
            }
        )
    return {"projects_root": "projects", "projects": projects}

