# shared/schemas.py

from __future__ import annotations

from typing import Any, Dict, List, Tuple


PlanJSON = Dict[str, Any]


def _is_non_empty_str(x: Any) -> bool:
    return isinstance(x, str) and len(x.strip()) > 0


def validate_plan_json(plan: Any) -> Tuple[bool, List[str]]:
    """
    Lightweight, dependency-free validator for the PM plan contract.
    Returns: (is_valid, errors)
    """
    errors: List[str] = []

    if not isinstance(plan, dict):
        return False, ["Plan must be a JSON object."]

    # Required top-level fields
    for key in ["summary", "target_files", "constraints", "validation"]:
        if key not in plan:
            errors.append(f"Missing required field: '{key}'.")

    if errors:
        return False, errors

    if not _is_non_empty_str(plan.get("summary")):
        errors.append("Field 'summary' must be a non-empty string.")

    # target_files
    target_files = plan.get("target_files")
    if not isinstance(target_files, list) or len(target_files) == 0:
        errors.append("Field 'target_files' must be a non-empty array.")
    else:
        for i, tf in enumerate(target_files):
            if not isinstance(tf, dict):
                errors.append(f"target_files[{i}] must be an object.")
                continue

            for k in ["file_name", "expected_path_hint", "modification_type", "details"]:
                if not _is_non_empty_str(tf.get(k)):
                    errors.append(f"target_files[{i}].{k} must be a non-empty string.")

    # constraints
    constraints = plan.get("constraints")
    if not isinstance(constraints, list) or any(not _is_non_empty_str(x) for x in constraints):
        errors.append("Field 'constraints' must be an array of non-empty strings.")

    # validation
    validation = plan.get("validation")
    if not isinstance(validation, list) or any(not _is_non_empty_str(x) for x in validation):
        errors.append("Field 'validation' must be an array of non-empty strings.")

    return (len(errors) == 0), errors
