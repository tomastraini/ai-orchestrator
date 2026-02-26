from __future__ import annotations

import ast
import os
from typing import Any, Dict, List


def _balanced(text: str, opening: str, closing: str) -> bool:
    count = 0
    for ch in text:
        if ch == opening:
            count += 1
        elif ch == closing:
            count -= 1
        if count < 0:
            return False
    return count == 0


def _syntax_sanity(path: str, content: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        try:
            ast.parse(content)
            return True
        except Exception:
            return False
    if ext in {".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts", ".cs", ".cpp", ".cc", ".cxx", ".h", ".hpp"}:
        if not _balanced(content, "(", ")"):
            return False
        if not _balanced(content, "{", "}"):
            return False
    return True


def validate_pre_apply(
    *,
    path: str,
    modification_type: str,
    creation_policy: str,
    exists_before: bool,
) -> Dict[str, Any]:
    checks: List[str] = []
    warnings: List[str] = []
    errors: List[str] = []
    update_like = modification_type in {"update", "replace", "modify", "patch", "rename", "move", "rename_file", "mv"}
    if (creation_policy == "must_exist" or update_like) and not exists_before:
        # Recovery/discovery logic may still resolve this target later.
        warnings.append("target_missing_before_apply")
    if os.path.isdir(path) and modification_type not in {"create_directory", "mkdir", "create_dir"}:
        # Path-type mismatch can be recovered by target resolution.
        warnings.append("target_is_directory_pre_apply")
    checks.append("target_policy_checked")
    return {"passed": not errors, "checks": checks, "warnings": warnings, "errors": errors}


def validate_post_apply(
    *,
    path: str,
    before_content: str,
    after_content: str,
    action: str,
) -> Dict[str, Any]:
    checks: List[str] = []
    warnings: List[str] = []
    errors: List[str] = []
    checks.append("syntax_sanity_checked")
    if action in {"updated_file", "created_file", "renamed_file"}:
        if action != "renamed_file" and before_content == after_content:
            errors.append("no_content_delta")
    if action in {"updated_file", "created_file", "observed_file"}:
        if not _syntax_sanity(path, after_content):
            errors.append("syntax_sanity_failed")
    return {"passed": not errors, "checks": checks, "warnings": warnings, "errors": errors}
