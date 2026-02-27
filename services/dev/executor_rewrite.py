from __future__ import annotations

import os
import re

from services.dev.command_policy import normalize_command_for_stack, normalize_non_interactive
from services.dev.executor_scope import _normalize_scope_path


def classify_failure(stdout: str, stderr: str, exit_code: int) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if "operation cancelled" in text or "operation canceled" in text or "aborted" in text:
        return "operation_cancelled"
    if "ok to proceed?" in text or "npm error canceled" in text or "prompt" in text:
        return "interactive_prompt"
    if (
        "not recognized as an internal or external command" in text
        or "command not found" in text
        or re.search(r"\bnot found\b", text) is not None
    ):
        return "command_not_found"
    if "no such file or directory" in text or "cannot find the path specified" in text or "enoent" in text:
        return "path_issue"
    if (
        "syntaxerror" in text
        or "unexpected token" in text
        or "parse error" in text
        or "error ts" in text
        or "typescript" in text
        or "esbuild" in text
    ):
        return "syntax_or_compile_error"
    if "cannot find module" in text:
        return "module_resolution_error"
    if "test failed" in text or "assertionerror" in text or "failing tests" in text:
        return "test_failure"
    if "config" in text or "tsconfig" in text or "package.json" in text or "pyproject" in text:
        return "config_error"
    if exit_code != 0 and any(tok in text for tok in ["package manager mismatch", "unsupported package manager", "unknown package manager"]):
        return "package_manager_mismatch"
    if exit_code != 0:
        return "unknown"
    return "none"

def rewrite_command_deterministic(
    command: str,
    category: str,
    stack_hint: str = "generic",
    *,
    scope_root: str = "",
    cwd: str = "",
) -> str:
    cmd = command.strip()
    low = cmd.lower()

    # Always strip brittle chained cwd changes; cwd is handled by executor.
    if "&&" in low:
        segments = [seg.strip() for seg in cmd.split("&&") if seg.strip()]
        filtered: List[str] = []
        for seg in segments:
            seg_low = seg.lower()
            if seg_low.startswith("cd "):
                continue
            if seg_low.startswith("mkdir ") or seg_low.startswith("mkdir -p "):
                continue
            filtered.append(seg)
        cmd = filtered[0] if filtered else ""
        low = cmd.lower()

    def _normalize_projects_target_token(token: str, cwd_hint: str) -> str:
        target = token.strip().replace("\\", "/").lstrip("./")
        cwd_norm = cwd_hint.strip().replace("\\", "/").lstrip("./")
        if not target.startswith("projects/"):
            return token
        # If cwd is projects root, keep child path (projects/foo -> foo), never collapse to "."
        if cwd_norm.endswith("/projects"):
            return target.split("/", 1)[1] if "/" in target else target
        if cwd_norm == "projects":
            return target.split("/", 1)[1] if "/" in target else target
        if cwd_norm in {"", "."}:
            return "."
        if target == cwd_norm:
            return "."
        if cwd_norm and target.startswith(f"{cwd_norm}/"):
            return target[len(cwd_norm) + 1 :] or "."
        return token

    # Keep deterministic rewrites generic in v2 (no framework-specific mutation).
    scope_abs = _normalize_scope_path(scope_root) if scope_root else ""
    cwd_rel = ""
    if cwd and scope_abs:
        try:
            cwd_rel = os.path.relpath(_normalize_scope_path(cwd), scope_abs).replace("\\", "/")
        except Exception:
            cwd_rel = ""
    parts = cmd.split()
    for i, tok in enumerate(parts):
        if tok.strip().replace("\\", "/").lstrip("./").startswith("projects/"):
            parts[i] = _normalize_projects_target_token(parts[i], cwd_rel or ".")
    cmd = " ".join(parts) if parts else cmd
    low = cmd.lower()

    if category == "interactive_prompt":
        if "--yes" not in low and "--force" not in low:
            return f"{cmd} --yes"

    return normalize_command_for_stack(normalize_non_interactive(cmd), stack_hint)
