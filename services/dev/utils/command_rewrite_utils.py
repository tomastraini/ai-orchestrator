from __future__ import annotations

import os
from typing import List

from services.dev.command_policy import detect_stack_from_command, normalize_command_for_stack, normalize_non_interactive


def is_likely_long_running_command(command: str) -> bool:
    low = f" {str(command or '').lower()} "
    tokens = [
        " npm run dev ",
        " npm start ",
        " pnpm dev ",
        " yarn dev ",
        " vite ",
        " next dev ",
        " flask run ",
        " uvicorn ",
        " rails server ",
        " dotnet watch ",
    ]
    return any(token in low for token in tokens)


def classify_failure(stdout: str, stderr: str, exit_code: int) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if "ok to proceed?" in text or "npm error canceled" in text or "prompt" in text:
        return "interactive_prompt"
    if "not recognized as an internal or external command" in text or "command not found" in text:
        return "command_not_found"
    if "no such file or directory" in text or "cannot find the path specified" in text:
        return "path_issue"
    if "package manager" in text or "npm" in text or "yarn" in text or "pnpm" in text:
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

    if "create-react-app" in low:
        parts = cmd.split()
        try:
            idx = next(i for i, tok in enumerate(parts) if "create-react-app" in tok.lower())
            if len(parts) > idx + 1:
                parts[idx + 1] = _normalize_projects_target_token(parts[idx + 1], cwd_rel or ".")
                cmd = " ".join(parts)
                low = cmd.lower()
        except StopIteration:
            pass
        if "--use-npm" not in low:
            cmd = f"{cmd} --use-npm"
        return cmd

    if "create-vite" in low or ("npm create" in low and "vite" in low) or ("npm init" in low and "vite" in low):
        parts = cmd.split()
        target_idx = -1
        if any("create-vite" in tok.lower() for tok in parts):
            for i, tok in enumerate(parts):
                if "create-vite" in tok.lower():
                    if len(parts) > i + 1 and not parts[i + 1].startswith("-"):
                        target_idx = i + 1
                    break
        else:
            for i, tok in enumerate(parts):
                if (tok.lower() == "create" or tok.lower() == "init") and len(parts) > i + 2:
                    candidate_tool = parts[i + 1].lower()
                    candidate_target = parts[i + 2]
                    if "vite" in candidate_tool and not candidate_target.startswith("-"):
                        target_idx = i + 2
                        break
        if target_idx >= 0:
            parts[target_idx] = _normalize_projects_target_token(parts[target_idx], cwd_rel or ".")
            cmd = " ".join(parts)
        return normalize_command_for_stack(normalize_non_interactive(cmd), stack_hint)

    if "nest new" in low and "@nestjs/cli" not in low:
        parts = cmd.split()
        app_name = "app"
        if len(parts) >= 3:
            app_name = parts[2]
        return f"npx @nestjs/cli new {app_name} --package-manager npm --skip-git"

    if "@nestjs/cli new" in low:
        if "--package-manager" not in low:
            cmd = f"{cmd} --package-manager npm"
        if "--skip-git" not in low:
            cmd = f"{cmd} --skip-git"
        return cmd

    if category == "interactive_prompt":
        if low.startswith("npx ") and "--yes" not in low:
            return f"npx --yes {cmd[4:].strip()}"
        if " npm " in f" {low} " and "--yes" not in low:
            return f"{cmd} --yes"

    inferred = stack_hint or detect_stack_from_command(command)
    return normalize_command_for_stack(normalize_non_interactive(cmd), inferred)

