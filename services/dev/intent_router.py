from __future__ import annotations

from typing import Any, Dict


INTENT_ANALYSIS = "analysis_explain"
INTENT_ARTIFACT = "artifact_generation"
INTENT_CODE = "code_modification"
INTENT_EXECUTION = "execution_only"


def route_plan_intent(plan: Dict[str, Any]) -> str:
    summary = str(plan.get("summary", "")).lower()
    targets = plan.get("target_files", [])
    bootstrap = plan.get("bootstrap_commands", [])
    validations = plan.get("validation", [])
    if isinstance(targets, list) and targets:
        return INTENT_CODE
    if isinstance(bootstrap, list) and bootstrap:
        return INTENT_EXECUTION
    if isinstance(validations, list) and validations and ("document" in summary or "adr" in summary):
        return INTENT_ARTIFACT
    if any(marker in summary for marker in {"explain", "analyze", "architecture", "tradeoff", "overview"}):
        return INTENT_ANALYSIS
    return INTENT_CODE

