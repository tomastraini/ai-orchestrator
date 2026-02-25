# shared/schemas.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


PlanJSON = Dict[str, Any]

ALLOWED_TOP_LEVEL_KEYS = {
    "summary",
    "project_mode",
    "project_ref",
    "stack",
    "bootstrap_commands",
    "target_files",
    "constraints",
    "validation",
    "clarification_summary",
}
REQUIRED_TOP_LEVEL_KEYS = set(ALLOWED_TOP_LEVEL_KEYS)
ALLOWED_TARGET_FILE_KEYS = {
    "file_name",
    "expected_path_hint",
    "modification_type",
    "details",
}
ALLOWED_PROJECT_REF_KEYS = {"name", "path_hint"}
ALLOWED_STACK_KEYS = {"frontend", "backend", "language_preferences"}
ALLOWED_BOOTSTRAP_COMMAND_KEYS = {"cwd", "command", "purpose"}


def _is_non_empty_str(x: Any) -> bool:
    return isinstance(x, str) and len(x.strip()) > 0


def _is_projects_rooted(path: str) -> bool:
    normalized = path.replace("\\", "/").strip().lower()
    return normalized == "projects" or normalized.startswith("projects/")


def _validate_no_unknown_keys(
    data: Dict[str, Any], allowed_keys: set[str], object_name: str
) -> List[str]:
    errors: List[str] = []
    unknown_keys = sorted(set(data.keys()) - allowed_keys)
    if unknown_keys:
        errors.append(
            f"{object_name} contains unknown keys: {', '.join(repr(k) for k in unknown_keys)}."
        )
    return errors


def _require_list_of_non_empty_strings(
    value: Any, field_name: str, *, allow_empty: bool
) -> List[str]:
    if not isinstance(value, list):
        return [f"Field '{field_name}' must be an array of strings."]
    if not allow_empty and len(value) == 0:
        return [f"Field '{field_name}' must not be empty."]
    if any(not _is_non_empty_str(x) for x in value):
        return [f"Field '{field_name}' must be an array of non-empty strings."]
    return []


def _require_typescript_preference(
    stack: Dict[str, Any], requirement: Optional[str]
) -> List[str]:
    if requirement is None:
        return []

    req_l = requirement.lower()
    contradicts_ts = any(
        marker in req_l
        for marker in [
            "javascript only",
            "plain javascript",
            "vanilla javascript",
            "no typescript",
            "without typescript",
        ]
    )
    if contradicts_ts:
        return []

    langs = stack.get("language_preferences")
    if isinstance(langs, list) and any(
        isinstance(x, str) and x.strip().lower() == "typescript" for x in langs
    ):
        return []
    return [
        "Field 'stack.language_preferences' must include 'TypeScript' unless requirement contradicts it."
    ]


def validate_plan_json(plan: Any, requirement: Optional[str] = None) -> Tuple[bool, List[str]]:
    """
    Lightweight, dependency-free validator for the PM plan contract.
    Returns: (is_valid, errors)
    """
    errors: List[str] = []

    if not isinstance(plan, dict):
        return False, ["Plan must be a JSON object."]

    # Required top-level fields
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in plan:
            errors.append(f"Missing required field: '{key}'.")

    if errors:
        return False, errors

    errors.extend(_validate_no_unknown_keys(plan, ALLOWED_TOP_LEVEL_KEYS, "Plan"))

    if not _is_non_empty_str(plan.get("summary")):
        errors.append("Field 'summary' must be a non-empty string.")

    # project_mode
    project_mode = plan.get("project_mode")
    if project_mode not in ("new_project", "existing_project"):
        errors.append("Field 'project_mode' must be either 'new_project' or 'existing_project'.")

    # project_ref
    project_ref = plan.get("project_ref")
    if not isinstance(project_ref, dict):
        errors.append("Field 'project_ref' must be an object.")
    else:
        errors.extend(
            _validate_no_unknown_keys(project_ref, ALLOWED_PROJECT_REF_KEYS, "project_ref")
        )
        if not _is_non_empty_str(project_ref.get("name")):
            errors.append("Field 'project_ref.name' must be a non-empty string.")
        path_hint = project_ref.get("path_hint")
        if not (path_hint is None or _is_non_empty_str(path_hint)):
            errors.append("Field 'project_ref.path_hint' must be null or a non-empty string.")
        if (
            project_mode == "new_project"
            and isinstance(path_hint, str)
            and not _is_projects_rooted(path_hint)
        ):
            errors.append(
                "Field 'project_ref.path_hint' must be under 'projects/' when project_mode is 'new_project'."
            )
        if project_mode == "new_project" and path_hint is None:
            errors.append(
                "Field 'project_ref.path_hint' must be set when project_mode is 'new_project'."
            )

    # stack
    stack = plan.get("stack")
    if not isinstance(stack, dict):
        errors.append("Field 'stack' must be an object.")
    else:
        errors.extend(_validate_no_unknown_keys(stack, ALLOWED_STACK_KEYS, "stack"))
        if not _is_non_empty_str(stack.get("frontend")):
            errors.append("Field 'stack.frontend' must be a non-empty string.")
        backend = stack.get("backend")
        if not (backend is None or _is_non_empty_str(backend)):
            errors.append("Field 'stack.backend' must be null or a non-empty string.")
        errors.extend(
            _require_list_of_non_empty_strings(
                stack.get("language_preferences"),
                "stack.language_preferences",
                allow_empty=False,
            )
        )
        errors.extend(_require_typescript_preference(stack, requirement))

    # bootstrap_commands
    bootstrap_commands = plan.get("bootstrap_commands")
    if not isinstance(bootstrap_commands, list):
        errors.append("Field 'bootstrap_commands' must be an array.")
    else:
        if project_mode == "new_project" and len(bootstrap_commands) == 0:
            errors.append(
                "Field 'bootstrap_commands' must be non-empty when project_mode is 'new_project'."
            )
        for i, cmd in enumerate(bootstrap_commands):
            if not isinstance(cmd, dict):
                errors.append(f"bootstrap_commands[{i}] must be an object.")
                continue
            errors.extend(
                _validate_no_unknown_keys(
                    cmd, ALLOWED_BOOTSTRAP_COMMAND_KEYS, f"bootstrap_commands[{i}]"
                )
            )
            for k in ["cwd", "command", "purpose"]:
                if not _is_non_empty_str(cmd.get(k)):
                    errors.append(f"bootstrap_commands[{i}].{k} must be a non-empty string.")

    # target_files
    target_files = plan.get("target_files")
    if not isinstance(target_files, list) or len(target_files) == 0:
        errors.append("Field 'target_files' must be a non-empty array.")
    else:
        for i, tf in enumerate(target_files):
            if not isinstance(tf, dict):
                errors.append(f"target_files[{i}] must be an object.")
                continue
            errors.extend(
                _validate_no_unknown_keys(tf, ALLOWED_TARGET_FILE_KEYS, f"target_files[{i}]")
            )

            for k in ["file_name", "expected_path_hint", "modification_type", "details"]:
                if not _is_non_empty_str(tf.get(k)):
                    errors.append(f"target_files[{i}].{k} must be a non-empty string.")
            if (
                project_mode == "new_project"
                and _is_non_empty_str(tf.get("expected_path_hint"))
                and not _is_projects_rooted(str(tf.get("expected_path_hint")))
            ):
                errors.append(
                    f"target_files[{i}].expected_path_hint must be under 'projects/' for new projects."
                )

    # constraints
    errors.extend(
        _require_list_of_non_empty_strings(
            plan.get("constraints"),
            "constraints",
            allow_empty=False,
        )
    )

    # validation
    errors.extend(
        _require_list_of_non_empty_strings(
            plan.get("validation"),
            "validation",
            allow_empty=False,
        )
    )

    # clarification_summary (can be empty if no questions needed)
    errors.extend(
        _require_list_of_non_empty_strings(
            plan.get("clarification_summary"),
            "clarification_summary",
            allow_empty=True,
        )
    )

    return (len(errors) == 0), errors
