# shared/schemas.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


PlanJSON = Dict[str, Any]

ALLOWED_TOP_LEVEL_KEYS = {
    "summary",
    "project_mode",
    "project_ref",
    "stack",
    "pm_checklist",
    "bootstrap_commands",
    "target_files",
    "constraints",
    "validation",
    "clarification_summary",
    "product_contract",
    "ambiguities",
    "technical_preferences",
    "review_guidelines",
    "discovery_hints",
    "target_intents",
    "repo_structure_snapshot",
}
REQUIRED_TOP_LEVEL_KEYS = set(ALLOWED_TOP_LEVEL_KEYS)
REQUIRED_TOP_LEVEL_KEYS.remove("product_contract")
REQUIRED_TOP_LEVEL_KEYS.remove("ambiguities")
REQUIRED_TOP_LEVEL_KEYS.remove("technical_preferences")
REQUIRED_TOP_LEVEL_KEYS.remove("review_guidelines")
REQUIRED_TOP_LEVEL_KEYS.remove("discovery_hints")
REQUIRED_TOP_LEVEL_KEYS.remove("target_intents")
REQUIRED_TOP_LEVEL_KEYS.remove("repo_structure_snapshot")
ALLOWED_TARGET_FILE_KEYS = {
    "file_name",
    "expected_path_hint",
    "modification_type",
    "details",
    "creation_policy",
    "symbol_hints",
    "candidate_paths",
    "path_confidence",
    "entrypoint_candidate",
}
ALLOWED_PROJECT_REF_KEYS = {"name", "path_hint"}
ALLOWED_STACK_KEYS = {"frontend", "backend", "language_preferences"}
ALLOWED_BOOTSTRAP_COMMAND_KEYS = {"cwd", "command", "purpose"}
ALLOWED_PM_CHECKLIST_KEYS = {"project_scope", "architecture", "backend_required", "database_required"}
ALLOWED_TARGET_INTENT_KEYS = {
    "path_hint",
    "file_role",
    "change_type",
    "path_priority",
    "confidence",
    "rationale",
}


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
    _ = (stack, requirement)
    return []


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

    # pm_checklist (kept for compatibility but intentionally flexible in v2)
    pm_checklist = plan.get("pm_checklist")
    if not isinstance(pm_checklist, dict):
        errors.append("Field 'pm_checklist' must be an object.")
    else:
        errors.extend(
            _validate_no_unknown_keys(pm_checklist, ALLOWED_PM_CHECKLIST_KEYS, "pm_checklist")
        )
        for key in ["project_scope", "architecture", "backend_required", "database_required"]:
            value = pm_checklist.get(key)
            if value is not None and not _is_non_empty_str(value):
                errors.append(f"Field 'pm_checklist.{key}' must be a non-empty string when provided.")

    # bootstrap_commands
    bootstrap_commands = plan.get("bootstrap_commands")
    if not isinstance(bootstrap_commands, list):
        errors.append("Field 'bootstrap_commands' must be an array.")
    else:
        # In v2, bootstrap commands can be intentionally empty if DEV plans to create files directly.
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
    if not isinstance(target_files, list):
        errors.append("Field 'target_files' must be an array.")
    else:
        allowed_creation_policies = {"must_exist", "create_if_missing"}
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
            creation_policy = tf.get("creation_policy")
            if creation_policy is not None and not _is_non_empty_str(creation_policy):
                errors.append(f"target_files[{i}].creation_policy must be a non-empty string when provided.")
            if _is_non_empty_str(creation_policy) and str(creation_policy) not in allowed_creation_policies:
                errors.append(
                    f"target_files[{i}].creation_policy must be one of: "
                    f"{', '.join(sorted(allowed_creation_policies))}."
                )
            if (
                project_mode == "new_project"
                and _is_non_empty_str(tf.get("expected_path_hint"))
                and not _is_projects_rooted(str(tf.get("expected_path_hint")))
            ):
                errors.append(
                    f"target_files[{i}].expected_path_hint must be under 'projects/' for new projects."
                )
            symbol_hints = tf.get("symbol_hints")
            if symbol_hints is not None:
                errors.extend(
                    _require_list_of_non_empty_strings(
                        symbol_hints,
                        f"target_files[{i}].symbol_hints",
                        allow_empty=True,
                    )
                )
            candidate_paths = tf.get("candidate_paths")
            if candidate_paths is not None:
                if not isinstance(candidate_paths, list):
                    errors.append(f"target_files[{i}].candidate_paths must be an array when provided.")
                else:
                    for j, candidate in enumerate(candidate_paths):
                        if not isinstance(candidate, dict):
                            errors.append(
                                f"target_files[{i}].candidate_paths[{j}] must be an object."
                            )
                            continue
                        path_val = candidate.get("path")
                        if not _is_non_empty_str(path_val):
                            errors.append(
                                f"target_files[{i}].candidate_paths[{j}].path must be a non-empty string."
                            )
                        score_val = candidate.get("score")
                        if score_val is not None:
                            try:
                                score_num = float(score_val)
                            except Exception:
                                errors.append(
                                    f"target_files[{i}].candidate_paths[{j}].score must be numeric when provided."
                                )
                            else:
                                if score_num < 0.0 or score_num > 1.0:
                                    errors.append(
                                        f"target_files[{i}].candidate_paths[{j}].score must be within [0, 1]."
                                    )
            path_confidence = tf.get("path_confidence")
            if path_confidence is not None:
                try:
                    confidence_num = float(path_confidence)
                except Exception:
                    errors.append(f"target_files[{i}].path_confidence must be numeric when provided.")
                else:
                    if confidence_num < 0.0 or confidence_num > 1.0:
                        errors.append(f"target_files[{i}].path_confidence must be within [0, 1].")
            entrypoint_candidate = tf.get("entrypoint_candidate")
            if entrypoint_candidate is not None and not isinstance(entrypoint_candidate, bool):
                errors.append(
                    f"target_files[{i}].entrypoint_candidate must be a boolean when provided."
                )

    # constraints
    errors.extend(_require_list_of_non_empty_strings(plan.get("constraints"), "constraints", allow_empty=True))

    # validation
    errors.extend(_require_list_of_non_empty_strings(plan.get("validation"), "validation", allow_empty=True))

    # clarification_summary (can be empty if no questions needed)
    errors.extend(
        _require_list_of_non_empty_strings(
            plan.get("clarification_summary"),
            "clarification_summary",
            allow_empty=True,
        )
    )

    product_contract = plan.get("product_contract")
    if product_contract is not None:
        if not isinstance(product_contract, dict):
            errors.append("Field 'product_contract' must be an object when provided.")
        else:
            for key in ["goals", "acceptance_criteria", "non_goals"]:
                if key in product_contract and not isinstance(product_contract.get(key), list):
                    errors.append(f"Field 'product_contract.{key}' must be an array of strings.")
            for key in ["goals", "acceptance_criteria", "non_goals"]:
                if key in product_contract and any(not _is_non_empty_str(x) for x in product_contract.get(key, [])):
                    errors.append(f"Field 'product_contract.{key}' must contain non-empty strings only.")

    ambiguities = plan.get("ambiguities")
    if ambiguities is not None:
        errors.extend(
            _require_list_of_non_empty_strings(
                ambiguities,
                "ambiguities",
                allow_empty=True,
            )
        )

    review_guidelines = plan.get("review_guidelines")
    if review_guidelines is not None:
        errors.extend(_require_list_of_non_empty_strings(review_guidelines, "review_guidelines", allow_empty=True))

    technical_preferences = plan.get("technical_preferences")
    if technical_preferences is not None and not isinstance(technical_preferences, dict):
        errors.append("Field 'technical_preferences' must be an object when provided.")

    discovery_hints = plan.get("discovery_hints")
    if discovery_hints is not None:
        errors.extend(_require_list_of_non_empty_strings(discovery_hints, "discovery_hints", allow_empty=True))

    target_intents = plan.get("target_intents")
    if target_intents is not None:
        if not isinstance(target_intents, list):
            errors.append("Field 'target_intents' must be an array when provided.")
        else:
            for i, intent in enumerate(target_intents):
                if not isinstance(intent, dict):
                    errors.append(f"target_intents[{i}] must be an object.")
                    continue
                errors.extend(_validate_no_unknown_keys(intent, ALLOWED_TARGET_INTENT_KEYS, f"target_intents[{i}]"))
                for required_key in ["path_hint", "file_role", "change_type"]:
                    if not _is_non_empty_str(intent.get(required_key)):
                        errors.append(f"target_intents[{i}].{required_key} must be a non-empty string.")
                path_priority = intent.get("path_priority")
                if path_priority is not None:
                    try:
                        priority_num = int(path_priority)
                    except Exception:
                        errors.append(f"target_intents[{i}].path_priority must be integer when provided.")
                    else:
                        if priority_num < 1:
                            errors.append(f"target_intents[{i}].path_priority must be >= 1.")
                confidence = intent.get("confidence")
                if confidence is not None:
                    try:
                        confidence_num = float(confidence)
                    except Exception:
                        errors.append(f"target_intents[{i}].confidence must be numeric when provided.")
                    else:
                        if confidence_num < 0.0 or confidence_num > 1.0:
                            errors.append(f"target_intents[{i}].confidence must be within [0, 1].")

    repo_structure_snapshot = plan.get("repo_structure_snapshot")
    if repo_structure_snapshot is not None and not isinstance(repo_structure_snapshot, dict):
        errors.append("Field 'repo_structure_snapshot' must be an object when provided.")

    return (len(errors) == 0), errors
