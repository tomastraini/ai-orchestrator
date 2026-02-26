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


def classify_target_class(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").lower()
    leaf = os.path.basename(normalized)
    if leaf.startswith(".") or leaf in {"package.json", "pyproject.toml", "requirements.txt", "cargo.toml", "go.mod"}:
        return "config"
    # Prefer extension family first to avoid false "index.* => entrypoint" on templates.
    if any(token in leaf for token in [".html", ".htm", ".xml", ".tmpl", ".template"]):
        return "template"
    if any(token in leaf for token in [".css", ".scss", ".sass", ".less", ".styl"]):
        return "style"
    if any(token in leaf for token in ["main.", "index.", "program.cs", "app.py"]):
        return "entrypoint"
    if ".spec." in leaf or ".test." in leaf:
        return "test"
    if "module" in leaf:
        return "module"
    if "component" in leaf:
        return "component"
    return "source"


def infer_expected_target_class(expected_path_hint: str, file_name: str, details: str) -> str:
    joined = " ".join([str(expected_path_hint or ""), str(file_name or ""), str(details or "")]).lower()
    if any(token in joined for token in ["component", ".component", "ui view", "render"]):
        return "component"
    if any(token in joined for token in ["module", "ngmodule", "app.module"]):
        return "module"
    if any(token in joined for token in ["template", "html", ".component.html"]):
        return "template"
    if any(token in joined for token in ["style", "css", "scss"]):
        return "style"
    if any(token in joined for token in ["entrypoint", "bootstrap", "main.ts", "main.js", "index.ts", "index.js"]):
        return "entrypoint"
    if any(token in joined for token in ["test", "spec", "assert"]):
        return "test"
    if any(token in joined for token in ["config", "settings", "package.json", "tsconfig", "pyproject"]):
        return "config"
    return classify_target_class(file_name or expected_path_hint)


def validate_intent_alignment(
    *,
    expected_path_hint: str,
    file_name: str,
    details: str,
    selected_path: str,
) -> Dict[str, Any]:
    checks: List[str] = ["intent_target_class_checked"]
    warnings: List[str] = []
    errors: List[str] = []
    expected_class = infer_expected_target_class(expected_path_hint, file_name, details)
    selected_class = classify_target_class(selected_path)
    confidence = 0.55
    hard_mismatch_pairs = {
        ("component", "entrypoint"),
        ("module", "entrypoint"),
        ("template", "entrypoint"),
        ("entrypoint", "component"),
        ("entrypoint", "module"),
        ("entrypoint", "template"),
        ("config", "entrypoint"),
    }
    if expected_class == selected_class:
        confidence = 0.95
    elif (expected_class, selected_class) in hard_mismatch_pairs:
        confidence = 0.95
        errors.append("intent_target_class_mismatch")
    else:
        confidence = 0.7
        warnings.append("intent_target_class_uncertain")
    return {
        "passed": not errors,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "confidence": confidence,
        "expected_target_class": expected_class,
        "selected_target_class": selected_class,
    }


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
