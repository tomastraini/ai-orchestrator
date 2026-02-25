from __future__ import annotations

import os
from typing import Any, Dict, List

from shared.pathing import normalize_rel_path


PROJECT_MARKERS = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
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
    for marker in PROJECT_MARKERS:
        if os.path.exists(os.path.join(project_path, marker)):
            markers.append(marker)
    return markers


def _collect_key_dirs(project_path: str) -> List[str]:
    key_dirs: List[str] = []
    for hint in KEY_DIR_HINTS:
        if os.path.isdir(os.path.join(project_path, hint)):
            key_dirs.append(hint)
    return key_dirs


def scan_projects_root(projects_root: str) -> Dict[str, Any]:
    projects: List[Dict[str, Any]] = []
    if not os.path.isdir(projects_root):
        return {"projects_root": normalize_rel_path(projects_root), "projects": []}

    for name in sorted(os.listdir(projects_root)):
        abs_path = os.path.join(projects_root, name)
        if not os.path.isdir(abs_path) or name.startswith("."):
            continue
        markers = _collect_markers(abs_path)
        key_dirs = _collect_key_dirs(abs_path)
        projects.append(
            {
                "name": name,
                "path_hint": f"projects/{name}",
                "markers": markers,
                "key_dirs": key_dirs,
                "top_entries": _list_top(abs_path),
            }
        )
    return {"projects_root": "projects", "projects": projects}

