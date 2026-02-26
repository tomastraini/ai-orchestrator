from __future__ import annotations

import hashlib
import os
from typing import List


def is_within_scope(scope_root: str, candidate_path: str) -> bool:
    try:
        scope_abs = os.path.abspath(scope_root)
        cand_abs = os.path.abspath(candidate_path)
        return os.path.commonpath([scope_abs, cand_abs]) == scope_abs
    except Exception:
        return False


def has_project_marker(path: str, marker_files: List[str]) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        names = set(os.listdir(path))
    except Exception:
        return False
    if any(marker in names for marker in marker_files):
        return True
    return any(name.endswith(".csproj") or name.endswith(".sln") for name in names)


def source_hint_count(path: str, source_hints: List[str]) -> int:
    if not os.path.isdir(path):
        return 0
    count = 0
    for name in source_hints:
        if os.path.isdir(os.path.join(path, name)):
            count += 1
    return count


def normalize_target_tail(expected_path_hint: str, project_name: str) -> str:
    normalized = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
    if normalized.startswith("projects/"):
        parts = [p for p in normalized.split("/") if p]
        if len(parts) >= 3:
            if project_name and parts[1] != project_name:
                return "/".join(parts[2:])
            return "/".join(parts[2:])
        return ""
    return normalized


def file_sha1(path: str) -> str:
    if not os.path.exists(path) or not os.path.isfile(path):
        return ""
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def resolve_target_file_path(
    *,
    scope_root: str,
    project_root: str,
    active_project_root: str,
    expected_path_hint: str,
    file_name: str,
) -> str:
    scope_abs = os.path.abspath(os.path.normpath(scope_root))
    expected_norm = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
    project_root_norm = (project_root or "").replace("\\", "/").strip().lstrip("./")
    file_name_norm = (file_name or "").strip()

    project_name = ""
    if project_root_norm.startswith("projects/"):
        parts = [p for p in project_root_norm.split("/") if p]
        if len(parts) >= 2:
            project_name = parts[1]

    if active_project_root:
        active_abs = os.path.abspath(os.path.normpath(active_project_root))
        if os.path.commonpath([scope_abs, active_abs]) == scope_abs:
            base_root = active_abs
        else:
            raise RuntimeError(f"Active project root escapes scope: {active_project_root}")
    else:
        rel = project_root_norm.split("/", 1)[1] if project_root_norm.startswith("projects/") else project_root_norm
        base_root = os.path.abspath(os.path.join(scope_abs, rel))

    file_name_norm = file_name_norm.replace("\\", "/").strip().lstrip("./")
    file_leaf = os.path.basename(file_name_norm) if file_name_norm else ""
    rel_path = expected_norm
    if expected_norm.startswith("projects/"):
        parts = [p for p in expected_norm.split("/") if p]
        if len(parts) >= 3:
            if project_name and parts[1] != project_name:
                rel_path = "/".join(parts[2:])
            else:
                rel_path = "/".join(parts[2:])
        elif len(parts) == 2:
            rel_path = file_name_norm or parts[-1]
        else:
            rel_path = file_name_norm
    elif not rel_path:
        rel_path = file_name_norm

    if not rel_path and file_leaf:
        rel_path = file_leaf

    expected_has_extension = bool(os.path.splitext(rel_path)[1]) if rel_path else False
    if rel_path and not expected_has_extension and file_leaf:
        rel_parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
        if not rel_parts or rel_parts[-1] != file_leaf:
            rel_path = "/".join(rel_parts + [file_leaf]) if rel_parts else file_leaf

    safe_path = os.path.abspath(os.path.join(base_root, rel_path))
    if os.path.commonpath([scope_abs, safe_path]) != scope_abs:
        raise RuntimeError(f"Implementation target escapes scope: {expected_path_hint}")
    return safe_path


def discover_existing_path(active_root: str, expected_path_hint: str, file_name: str) -> str:
    expected_norm = expected_path_hint.replace("\\", "/").strip().lstrip("./")
    targets = [name for name in {file_name.strip(), os.path.basename(expected_norm)} if name]
    expected_suffix = ""
    if expected_norm.startswith("projects/"):
        parts = [p for p in expected_norm.split("/") if p]
        if len(parts) >= 3:
            expected_suffix = "/".join(parts[2:])
    else:
        expected_suffix = expected_norm

    best_candidate = ""
    best_score = -1
    for root, dirs, files in os.walk(active_root):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".git", ".venv", "__pycache__", "dist", "build", ".next"}]
        for name in files:
            if name not in targets:
                continue
            candidate = os.path.join(root, name)
            normalized = candidate.replace("\\", "/")
            if expected_suffix and normalized.endswith(expected_suffix):
                return candidate
            score = 0
            if expected_suffix:
                exp_dirs = expected_suffix.split("/")[:-1]
                rel = os.path.relpath(candidate, active_root).replace("\\", "/")
                rel_dirs = rel.split("/")[:-1]
                for i, part in enumerate(exp_dirs):
                    if i < len(rel_dirs) and rel_dirs[i] == part:
                        score += 1
            else:
                score = 100 - len(os.path.relpath(candidate, active_root).split(os.sep))
            if score > best_score:
                best_candidate = candidate
                best_score = score
    return best_candidate

