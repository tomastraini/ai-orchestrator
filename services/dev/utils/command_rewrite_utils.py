from __future__ import annotations

import os
from typing import List

from services.dev.command_policy import detect_stack_from_command, normalize_command_for_stack, normalize_non_interactive


def is_likely_long_running_command(command: str) -> bool:
    low = str(command or "").lower()
    return any(
        hint in low
        for hint in [" run dev", " start", " serve", " watch", " --watch", " --live-reload", " server"]
    )


def classify_failure(stdout: str, stderr: str, exit_code: int) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if "ok to proceed?" in text or "npm error canceled" in text or "prompt" in text:
        return "interactive_prompt"
    if (
        "not recognized as an internal or external command" in text
        or "command not found" in text
        or "not found" in text
    ):
        return "command_not_found"
    if "no such file or directory" in text or "cannot find the path specified" in text:
        return "path_issue"
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
        if cwd_norm.endswith("/projects") or cwd_norm == "projects":
            return target.split("/", 1)[1] if "/" in target else target
        if cwd_norm in {"", "."}:
            return "."
        if target == cwd_norm:
            return "."
        if cwd_norm and target.startswith(f"{cwd_norm}/"):
            return target[len(cwd_norm) + 1 :] or "."
        return token

    scope_abs = os.path.abspath(os.path.normpath(scope_root)) if scope_root else ""
    cwd_rel = ""
    if cwd and scope_abs:
        try:
            cwd_rel = os.path.relpath(os.path.abspath(os.path.normpath(cwd)), scope_abs).replace("\\", "/")
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

    inferred = stack_hint or detect_stack_from_command(command)
    return normalize_command_for_stack(normalize_non_interactive(cmd), inferred)

