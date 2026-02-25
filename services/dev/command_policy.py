from __future__ import annotations

from typing import Tuple


RISKY_TOKENS = (
    " rm -rf ",
    " del /f ",
    " format ",
    " mkfs",
    " shutdown ",
    " reboot ",
    " --force",
    " -y ",
)


def normalize_non_interactive(command: str) -> str:
    cmd = (command or "").strip()
    low = f" {cmd.lower()} "
    if cmd.lower().startswith("npx ") and "--yes" not in low:
        return f"npx --yes {cmd[4:].strip()}"
    if "create-react-app" in low and "--use-npm" not in low:
        return f"{cmd} --use-npm"
    return cmd


def assess_risk(command: str) -> Tuple[bool, str]:
    low = f" {str(command).lower()} "
    for token in RISKY_TOKENS:
        if token in low:
            return True, f"matched risky token '{token.strip()}'"
    return False, ""

