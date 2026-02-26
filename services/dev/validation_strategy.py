from __future__ import annotations

from typing import Any, Dict, List


def default_validation_followup_options() -> List[Dict[str, str]]:
    return [
        {
            "id": "manual_ui_walkthrough",
            "label": "Manual UI walkthrough",
            "description": "Run the app and verify behaviors manually with observed evidence.",
        },
        {
            "id": "browser_automation_adapter",
            "label": "Browser automation (if adapter available)",
            "description": "Use a browser capability adapter to run click/type/observe checks.",
        },
        {
            "id": "targeted_runtime_checks",
            "label": "Targeted runtime checks",
            "description": "Run narrow executable checks that validate key acceptance criteria.",
        },
        {
            "id": "poke_tests",
            "label": "Runtime poke tests",
            "description": "Probe likely failure modes with lightweight interactive checks.",
        },
    ]


def infer_validation_strategy(
    *,
    raw_validation_requirements: List[str],
    validation_commands: List[str],
    unresolved_validation_requirements: List[Dict[str, Any]],
    browser_adapter_available: bool = False,
) -> Dict[str, Any]:
    has_raw_requirements = bool(raw_validation_requirements)
    has_executable_validation = bool(validation_commands)
    has_unresolved_requirements = bool(unresolved_validation_requirements)
    requires_clarification = has_raw_requirements and has_unresolved_requirements and not has_executable_validation

    mode = "executable"
    if requires_clarification:
        mode = "manual_or_browser_clarification"
    elif has_raw_requirements and not has_executable_validation:
        mode = "manual_default"

    return {
        "mode": mode,
        "requires_clarification": requires_clarification,
        "browser_adapter_available": bool(browser_adapter_available),
        "unresolved_requirements_count": len(unresolved_validation_requirements),
        "followup_options": default_validation_followup_options(),
    }
