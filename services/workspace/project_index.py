from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from services.workspace.cognition.index_builder import build_cognition_index as build_cognition_index_v2
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


def scan_workspace_context(repo_root: str, *, file_limit: int = 400) -> Dict[str, Any]:
    """
    Lightweight, read-only workspace discovery for PM/DEV reasoning.
    """
    projects_root = os.path.join(repo_root, "projects")
    projects_snapshot = scan_projects_root(projects_root)
    sampled_files: List[str] = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [
            d
            for d in dirs
            if d not in {".git", ".venv", "__pycache__", "node_modules", ".cursor", ".orchestrator"}
        ]
        for name in files:
            rel = normalize_rel_path(os.path.relpath(os.path.join(root, name), repo_root))
            sampled_files.append(rel)
            if len(sampled_files) >= file_limit:
                break
        if len(sampled_files) >= file_limit:
            break
    extension_histogram: Dict[str, int] = {}
    for rel in sampled_files:
        ext = os.path.splitext(rel)[1].lower() or "<no_ext>"
        extension_histogram[ext] = extension_histogram.get(ext, 0) + 1
    return {
        "repo_root": normalize_rel_path(repo_root),
        "projects_snapshot": projects_snapshot,
        "sampled_files": sampled_files,
        "extension_histogram": extension_histogram,
    }


def rank_candidate_files(requirement: str, files: List[str], *, top_k: int = 40) -> List[Dict[str, Any]]:
    """
    Likelihood-based ranking from requirement tokens against candidate files.
    """
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", requirement or "") if len(t) >= 3]
    scored: List[Dict[str, Any]] = []
    for rel in files:
        rel_low = rel.lower()
        score = 0
        hits: List[str] = []
        for tok in tokens:
            if tok in rel_low:
                score += 5
                hits.append(tok)
        if rel_low.endswith(("readme.md", "package.json", "pyproject.toml", "requirements.txt")):
            score += 1
        if score > 0:
            scored.append({"path": rel, "score": score, "hits": sorted(set(hits))})
    scored.sort(key=lambda x: int(x.get("score", 0)), reverse=True)
    return scored[: max(1, int(top_k))]


def build_cognition_index(active_root: str, rel_files: List[str]) -> Dict[str, Any]:
    rel_list = [normalize_rel_path(str(item)) for item in rel_files if str(item).strip()]
    return build_cognition_index_v2(active_root, rel_list)

