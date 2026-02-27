from __future__ import annotations

from typing import List, Optional


def _is_blocked_command(command: str) -> bool:
    _ = command
    return False

def _violates_constraints(command: str, constraints: List[str]) -> Optional[str]:
    low_cmd = f" {command.lower()} "
    for raw_constraint in constraints:
        constraint = str(raw_constraint or "").strip().lower()
        if not constraint:
            continue
        if ("no git push" in constraint or "do not push" in constraint) and " git push " in low_cmd:
            return f"violates constraint '{raw_constraint}'"
        if "no git" in constraint and " git " in low_cmd:
            return f"violates constraint '{raw_constraint}'"
        if (
            "no dev server" in constraint
            or "do not run dev server" in constraint
            or "do not start server" in constraint
            or "no start command" in constraint
        ) and any(token in low_cmd for token in [" run dev ", " start ", " serve ", " watch ", " --watch ", " server "]):
            return f"violates constraint '{raw_constraint}'"
        if ("no install" in constraint or "do not install" in constraint) and any(
            token in low_cmd for token in [" install ", " add ", " restore "]
        ):
            return f"violates constraint '{raw_constraint}'"
    return None

def _is_likely_long_running_command(command: str) -> bool:
    low = str(command or "").lower()
    return any(
        hint in low
        for hint in [" run dev", " start", " serve", " watch", " --watch", " --live-reload", " server"]
    )
