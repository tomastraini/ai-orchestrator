from __future__ import annotations

import os
from typing import Optional


def normalize_rel_path(path: str) -> str:
    raw = (path or "").replace("\\", "/").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    while "//" in raw:
        raw = raw.replace("//", "/")
    return raw.strip("/")


def _collapse_nested_projects_segments(path: str) -> str:
    parts = [p for p in normalize_rel_path(path).split("/") if p]
    if not parts:
        return ""
    if "projects" not in parts:
        return "/".join(parts)
    first = parts.index("projects")
    trimmed = parts[first:]
    # Prevent accidental nesting like projects/foo/projects/foo.
    if len(trimmed) >= 4:
        for idx in range(2, len(trimmed) - 1):
            if trimmed[idx] == "projects":
                return "/".join(trimmed[:2])
    return "/".join(trimmed)


def canonical_projects_path(path: Optional[str], default_path: str) -> str:
    default_norm = _collapse_nested_projects_segments(default_path)
    raw = _collapse_nested_projects_segments(path or "")
    if not raw:
        return default_norm
    if raw == "projects":
        return raw
    if not raw.startswith("projects/"):
        return default_norm
    return raw


def project_name_from_path(path: str, fallback: str = "project") -> str:
    parts = [p for p in normalize_rel_path(path).split("/") if p]
    if len(parts) >= 2 and parts[0] == "projects":
        return parts[1]
    return fallback


def is_within_scope(scope_root: str, candidate_path: str) -> bool:
    scope_abs = os.path.abspath(os.path.normpath(scope_root))
    cand_abs = os.path.abspath(os.path.normpath(candidate_path))
    return os.path.commonpath([scope_abs, cand_abs]) == scope_abs

