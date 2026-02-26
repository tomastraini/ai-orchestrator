from __future__ import annotations

import difflib
import os
from typing import Any, Dict, Optional, Tuple

from services.dev.edit_validator import validate_post_apply, validate_pre_apply
from services.dev.types.dev_graph_state import DevGraphState


def run(
    *,
    state: DevGraphState,
    graph_cls: type,
    idx: int,
    target: Dict[str, str],
    pass_index: int,
    scope_root: str,
    project_root: str,
    active_root: str,
    file_index: Optional[Dict[str, Any]],
    target_proofs: Dict[str, Dict[str, Any]],
) -> Tuple[str, str]:
    expected = str(target.get("expected_path_hint", ""))
    modification_type = str(target.get("modification_type", "")).lower()
    creation_policy = str(target.get("creation_policy", "")).strip().lower() or (
        "must_exist" if modification_type in {"update", "replace", "modify", "patch", "verify"} else "create_if_missing"
    )
    details = str(target.get("details", "")).strip()
    raw_file_name = str(target.get("file_name", "")).strip()
    file_name = os.path.basename(raw_file_name.replace("\\", "/")) if raw_file_name else os.path.basename(expected)

    safe_target = graph_cls._resolve_target_file_path(
        scope_root=scope_root,
        project_root=project_root,
        active_project_root=active_root,
        expected_path_hint=expected,
        file_name=file_name,
    )
    key = f"todo_impl_{idx}"
    if key not in target_proofs:
        target_proofs[key] = {
            "before_hash": graph_cls._file_sha1(safe_target),
            "after_hash": "",
            "before_path": safe_target,
            "after_path": "",
            "action": "",
        }
    before_text = ""
    if os.path.exists(safe_target) and os.path.isfile(safe_target):
        try:
            with open(safe_target, "r", encoding="utf-8", errors="ignore") as fh:
                before_text = fh.read()
        except Exception:
            before_text = ""
    pre_check = validate_pre_apply(
        path=safe_target,
        modification_type=modification_type,
        creation_policy=creation_policy,
        exists_before=os.path.exists(safe_target),
    )
    graph_cls._emit_event(
        state,
        "edit_precheck",
        pass_index=pass_index,
        path=graph_cls._relpath_safe(state, safe_target),
        passed=bool(pre_check.get("passed", False)),
        errors=pre_check.get("errors", []),
        checks=pre_check.get("checks", []),
    )
    if not bool(pre_check.get("passed", False)):
        raise RuntimeError(f"Edit pre-check failed for {expected}: {pre_check.get('errors', [])}")

    action, action_note, resolved_target = graph_cls._apply_target_in_pass(
        state=state,
        safe_target=safe_target,
        file_name=file_name,
        active_root=active_root,
        modification_type=modification_type,
        details=details,
        pass_index=pass_index,
        expected_path_hint=expected,
        creation_policy=creation_policy,
        file_index=file_index,
    )
    if action == "path_type_mismatch":
        recovered_target = os.path.join(resolved_target, file_name) if file_name else resolved_target
        graph_cls._emit(
            state,
            f"[IMPLEMENTATION_RECOVERY] pass={pass_index} reason={action_note} old_target={resolved_target} new_target={recovered_target}",
        )
        action, action_note, resolved_target = graph_cls._apply_target_in_pass(
            state=state,
            safe_target=recovered_target,
            file_name=file_name,
            active_root=active_root,
            modification_type=modification_type,
            details=details,
            pass_index=pass_index,
            expected_path_hint=expected,
            creation_policy=creation_policy,
            file_index=file_index,
        )
    if action == "missing_expected_file":
        discovered = graph_cls._discover_existing_path(
            active_root,
            expected,
            file_name,
            project_root=project_root,
            file_index=file_index,
            state=state,
        )
        if not discovered:
            gaps = state.setdefault("capability_gaps", [])
            gaps.append(
                {
                    "type": "unknown_stack_or_path",
                    "expected_path_hint": expected,
                    "file_name": file_name,
                    "reason": "missing_expected_file_after_discovery",
                }
            )
            raise RuntimeError(f"Expected target missing and discovery failed: {expected}")
        graph_cls._emit(
            state,
            f"[IMPLEMENTATION_RECOVERY] pass={pass_index} reason=discovered_target old_target={resolved_target} new_target={discovered}",
        )
        action, action_note, resolved_target = graph_cls._apply_target_in_pass(
            state=state,
            safe_target=discovered,
            file_name=file_name,
            active_root=active_root,
            modification_type=modification_type,
            details=details,
            pass_index=pass_index,
            expected_path_hint=expected,
            creation_policy=creation_policy,
            file_index=file_index,
        )
        if action == "missing_expected_file":
            raise RuntimeError(f"Expected target missing and discovery failed: {expected}")
    if action == "low_signal_update_rejected":
        raise RuntimeError(f"Low-signal update rejected for {resolved_target}")
    if action == "invalid_operation":
        raise RuntimeError(f"Invalid operation for target {expected}: {action_note}")

    target_proofs[key]["after_hash"] = graph_cls._file_sha1(resolved_target)
    target_proofs[key]["after_path"] = resolved_target
    prev_action = str(target_proofs[key].get("action", ""))
    if not (prev_action == "renamed_file" and action == "observed_file"):
        target_proofs[key]["action"] = action
    state["touched_paths"].append(resolved_target)
    after_text = ""
    if os.path.exists(resolved_target) and os.path.isfile(resolved_target):
        try:
            with open(resolved_target, "r", encoding="utf-8", errors="ignore") as fh:
                after_text = fh.read()
        except Exception:
            after_text = ""
    post_check = validate_post_apply(
        path=resolved_target,
        before_content=before_text,
        after_content=after_text,
        action=action,
    )
    graph_cls._emit_event(
        state,
        "edit_postcheck",
        pass_index=pass_index,
        path=graph_cls._relpath_safe(state, resolved_target),
        action=action,
        passed=bool(post_check.get("passed", False)),
        errors=post_check.get("errors", []),
        checks=post_check.get("checks", []),
    )
    if not bool(post_check.get("passed", False)):
        raise RuntimeError(f"Edit post-check failed for {expected}: {post_check.get('errors', [])}")
    diff_preview = "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True)[:80],
            after_text.splitlines(keepends=True)[:80],
            lineterm="",
        )
    )
    graph_cls._emit_event(
        state,
        "file_mutation",
        pass_index=pass_index,
        action=action,
        action_note=action_note,
        path=graph_cls._relpath_safe(state, resolved_target),
        before_size=len(before_text.encode("utf-8")),
        after_size=len(after_text.encode("utf-8")),
        diff_preview=graph_cls._sanitize_text(diff_preview, 800),
    )
    graph_cls._emit(state, f"[IMPLEMENTATION] pass={pass_index} action={action} target={resolved_target} note={action_note}")
    return action, resolved_target

