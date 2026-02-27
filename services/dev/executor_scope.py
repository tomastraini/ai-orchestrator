from __future__ import annotations

import os

from shared.pathing import _collapse_nested_projects_segments, canonicalize_scope_path


class DevExecutorError(RuntimeError):
    pass


def _normalize_scope_path(path: str) -> str:
    return os.path.abspath(os.path.normpath(path))

def _assert_within_scope(scope_root: str, candidate_path: str) -> str:
    scope_abs = _normalize_scope_path(scope_root)
    candidate_abs = _normalize_scope_path(candidate_path)
    if os.path.commonpath([scope_abs, candidate_abs]) != scope_abs:
        raise DevExecutorError(
            f"Path '{candidate_path}' escapes allowed scope '{scope_root}'."
        )
    return candidate_abs

def _resolve_cwd(scope_root: str, raw_cwd: str) -> str:
    raw = (raw_cwd or "").strip()
    if not raw or raw == "." or raw == "projects":
        return _assert_within_scope(scope_root, scope_root)

    raw_norm = _collapse_nested_projects_segments(raw.replace("\\", "/"))
    if raw_norm == "projects":
        raw_norm = "."
    while raw_norm.startswith("projects/"):
        raw_norm = raw_norm.split("/", 1)[1] if "/" in raw_norm else "."
    raw = raw_norm or "."

    if os.path.isabs(raw):
        return _assert_within_scope(scope_root, canonicalize_scope_path(scope_root, raw))
    return _assert_within_scope(scope_root, canonicalize_scope_path(scope_root, os.path.join(scope_root, raw)))
