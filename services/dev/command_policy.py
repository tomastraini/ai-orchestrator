from __future__ import annotations

from typing import Tuple


RISKY_TOKENS = (
    " git push ",
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
    if cmd.lower().startswith("dotnet new") and "--force" not in low:
        return f"{cmd} --force"
    return cmd


def detect_stack_from_command(command: str) -> str:
    low = str(command or "").lower()
    if "dotnet " in low or ".csproj" in low:
        return "dotnet"
    if "bundle " in low or "rails " in low or "gem " in low:
        return "ruby"
    if "pip " in low or "python " in low or "pytest" in low:
        return "python"
    if "npm " in low or "npx " in low or "yarn " in low or "pnpm " in low:
        return "node"
    return "generic"


def normalize_command_for_stack(command: str, stack: str) -> str:
    cmd = normalize_non_interactive(command)
    low = f" {cmd.lower()} "
    if stack == "python" and "pip install" in low and "--disable-pip-version-check" not in low:
        return f"{cmd} --disable-pip-version-check"
    return cmd


def assess_risk(command: str) -> Tuple[bool, str]:
    low = f" {str(command).lower()} "
    for token in RISKY_TOKENS:
        if token in low:
            return True, f"matched risky token '{token.strip()}'"
    return False, ""

