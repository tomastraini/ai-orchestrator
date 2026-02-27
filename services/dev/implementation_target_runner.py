from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from langgraph.graph import END, START, StateGraph

from services.dev.dev_executor import execute_dev_tasks, execute_single_recovery_command
from services.dev.edit_validator import validate_intent_alignment
from services.dev.phases.ask_cli_clarifications import run as ask_cli_clarifications_phase
from services.dev.phases.derive_dev_todos import run as derive_dev_todos_phase
from services.dev.phases.dev_preflight_planning import run as dev_preflight_planning_phase
from services.dev.phases.execute_bootstrap_phase import run as execute_bootstrap_phase
from services.dev.phases.execute_final_compile_gate import run as execute_final_compile_gate
from services.dev.phases.execute_implementation_phase import run as execute_implementation_phase
from services.dev.phases.execute_implementation_target import run as execute_implementation_target
from services.dev.phases.execute_validation_phase import run as execute_validation_phase
from services.dev.phases.finalize_result import run as finalize_result_phase
from services.dev.phases.ingest_pm_plan import run as ingest_pm_plan_phase
from services.dev.phases.prepare_execution_steps import run as prepare_execution_steps_phase
from services.dev.edit_primitives import patch_region, rename_path
from services.dev.types.dev_graph_state import DevGraphState
from services.workspace.cognition.scaffold_probe import probe_scaffold_layout
from services.workspace.cognition.snapshot_store import persist_cognition_snapshot
from services.workspace.project_index import build_cognition_index, detect_stack_from_markers, rank_candidate_files, scan_workspace_context
from shared.dev_schemas import DevChecklistItem, DevTask, derive_project_name
from shared.pathing import canonicalize_scope_path


class ImplementationTargetRunnerMixin:
    @staticmethod
    def _apply_target_in_pass(
        *,
        state: DevGraphState,
        safe_target: str,
        file_name: str,
        active_root: str,
        modification_type: str,
        details: str,
        pass_index: int,
        expected_path_hint: str,
        creation_policy: str,
        file_index: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, str]:
        if modification_type in {"create_directory", "mkdir", "create_dir"}:
            os.makedirs(safe_target, exist_ok=True)
            return "ensured_directory", f"pass={pass_index}", safe_target

        rename_like = modification_type in {"rename", "move", "rename_file", "mv"}
        if rename_like:
            if not os.path.exists(safe_target):
                discovered = DevMasterGraph._discover_existing_path(
                    active_root,
                    expected_path_hint,
                    file_name,
                    project_root=str(state.get("project_root", "")),
                    file_index=file_index,
                    state=state,
                )
                if discovered:
                    safe_target = discovered
            if not os.path.exists(safe_target):
                return "missing_expected_file", "rename_source_missing", safe_target
            destination = DevMasterGraph._infer_rename_destination(
                safe_target=safe_target,
                file_name=file_name,
                expected_path_hint=expected_path_hint,
                details=details,
            )
            if not destination:
                return "invalid_operation", "rename_destination_unresolved", safe_target
            changed, note = rename_path(src_path=safe_target, dest_path=destination)
            if not changed and note == "noop_same_path":
                return "observed_file", "rename_noop_same_path", safe_target
            if not changed:
                return "invalid_operation", f"rename_failed:{note}", safe_target
            state.setdefault("target_resolution_evidence", {})[expected_path_hint or file_name] = {
                "resolved_path": DevMasterGraph._relpath_safe(state, destination),
                "resolution_method": "rename_operation",
                "confidence": 0.95,
                "candidates_considered": [DevMasterGraph._relpath_safe(state, safe_target), DevMasterGraph._relpath_safe(state, destination)],
            }
            return "renamed_file", note, destination

        def _looks_like_file_target(path: str, file_hint: str) -> bool:
            hint_leaf = os.path.basename(str(file_hint or "").replace("\\", "/")).strip()
            path_leaf = os.path.basename(str(path or "").replace("\\", "/")).strip()
            if hint_leaf.startswith(".") or path_leaf.startswith("."):
                return True
            if os.path.splitext(hint_leaf)[1]:
                return True
            if os.path.splitext(path_leaf)[1]:
                return True
            return False

        is_directory_target = modification_type in {"create_directory", "mkdir", "create_dir"}
        if not is_directory_target and _looks_like_file_target(safe_target, file_name):
            is_directory_target = False
        elif not is_directory_target:
            is_directory_target = False

        if is_directory_target:
            os.makedirs(safe_target, exist_ok=True)
            return "ensured_path", f"pass={pass_index}", safe_target

        os.makedirs(os.path.dirname(safe_target), exist_ok=True)
        if os.path.isdir(safe_target):
            return "path_type_mismatch", "target_is_directory", safe_target
        update_like = modification_type in {"update", "replace", "modify", "patch"}
        verify_like = modification_type in {"verify", "inspect", "check"}
        must_exist = str(creation_policy or "").strip().lower() == "must_exist" or update_like or verify_like
        if update_like and not os.path.exists(safe_target):
            discovered = DevMasterGraph._discover_existing_path(
                active_root,
                expected_path_hint,
                file_name,
                project_root=str(state.get("project_root", "")),
                file_index=file_index,
                state=state,
            )
            if discovered:
                safe_target = discovered
            else:
                return "missing_expected_file", "requires_discovery_or_clarification", safe_target
        if verify_like and not os.path.exists(safe_target):
            discovered = DevMasterGraph._discover_existing_path(
                active_root,
                expected_path_hint,
                file_name,
                project_root=str(state.get("project_root", "")),
                file_index=file_index,
                state=state,
            )
            if discovered:
                return "observed_file", "verify_discovered_target", discovered
            return "missing_expected_file", "verify_missing_target", safe_target
        if not os.path.exists(safe_target):
            if must_exist:
                return "missing_expected_file", "not_created_due_to_update_policy", safe_target
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(
                    DevMasterGraph._llm_generate_file_content(
                        state=state,
                        target_path=safe_target,
                        file_name=file_name,
                        details=details,
                        existing_content="",
                    )
                )
            return "created_file", "generated_content", safe_target

        with open(safe_target, "r", encoding="utf-8", errors="ignore") as fh:
            original = fh.read()
        new_content = DevMasterGraph._llm_generate_file_content(
            state=state,
            target_path=safe_target,
            file_name=file_name,
            details=details,
            existing_content=original,
        )

        patched_content, changed = patch_region(original, new_content)
        if changed:
            low_signal = new_content.strip().casefold() in {
                original.strip().casefold(),
                f"// {details}".strip().casefold(),
                f"# {details}".strip().casefold(),
            }
            if low_signal:
                return "low_signal_update_rejected", "comment_or_noop_like_update", safe_target
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(patched_content)
            return "updated_file", "generated_update", safe_target

        if os.path.getsize(safe_target) == 0:
            with open(safe_target, "w", encoding="utf-8") as fh:
                fh.write(
                    DevMasterGraph._llm_generate_file_content(
                        state=state,
                        target_path=safe_target,
                        file_name=file_name,
                        details=details,
                        existing_content="",
                    )
                )
            return "updated_file", "filled_empty_file", safe_target
        return "observed_file", "already_up_to_date", safe_target

    @staticmethod
    def _run_implementation_review(state: DevGraphState, active_root: str, targets: List[Dict[str, str]]) -> List[str]:
        _ = (active_root, targets)
        findings: List[str] = []
        touched = [str(p) for p in state.get("touched_paths", []) if isinstance(p, str)]
        for path in touched:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except Exception:
                continue
            lowered = content.lower()
            if "implement pass" in lowered or "\n// implement:" in lowered or "\n# implement:" in lowered:
                findings.append(f"placeholder marker found in {DevMasterGraph._relpath_safe(state, path)}")
            if "todo" in lowered and len(content.strip()) < 220:
                findings.append(f"file appears TODO-only in {DevMasterGraph._relpath_safe(state, path)}")
        return findings

    @staticmethod
    def _execute_implementation_phase_impl(state: DevGraphState) -> DevGraphState:
        state["current_step"] = "execute_implementation_phase"
        DevMasterGraph._emit(state, "[PHASE_START] execute_implementation_phase")
        DevMasterGraph._emit(state, "[PHASE] implementation")
        DevMasterGraph._ensure_repository_memory(state)

        if state.get("bootstrap_status") == "failed":
            state["implementation_status"] = "impl_skipped"
            DevMasterGraph._emit(state, "[IMPLEMENTATION] skipped due to bootstrap_failed")
            state["phase_status"]["execute_implementation_phase"] = "skipped"
            return state

        scope_root = state["scope_root"]
        project_root = str(state.get("project_root", f"projects/{state.get('project_name', 'project')}"))
        active_root = str(state.get("active_project_root", "")).strip()
        if not active_root:
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            active_root = os.path.join(scope_root, rel)
        state["llm_context_contract"] = DevMasterGraph._build_llm_context_contract(state)
        DevMasterGraph._emit(state, f"[CONTEXT] active_root={active_root}")
        DevMasterGraph._refresh_active_root_index(state, category="implementation_index_refresh")
        targets = state.get("implementation_targets", [])
        pending_targets: List[Tuple[int, Dict[str, str]]] = []
        target_proofs: Dict[str, Dict[str, Any]] = {}
        for idx, target in enumerate(targets, start=1):
            item = DevMasterGraph._find_checklist_item(state, f"todo_impl_{idx}")
            if item and str(item.get("status", "")) == "completed":
                continue
            pending_targets.append((idx, target))
            checklist_id = f"todo_impl_{idx}"
            DevMasterGraph._set_checklist_status(
                state,
                checklist_id,
                "in_progress",
                evidence={"phase": "implementation", "pass": 0},
            )
        if not pending_targets:
            state["implementation_status"] = "completed"
            state["phase_status"]["execute_implementation_phase"] = "completed"
            DevMasterGraph._emit(state, "[IMPLEMENTATION] no pending implementation checklist items")
            return state
        total_actions = 0
        failed_target_ids: List[str] = []
        try:
            for pass_index in (1, 2):
                pass_label = f"implementation_pass_{pass_index}"
                DevMasterGraph._emit(state, f"[PHASE] {pass_label}")
                DevMasterGraph._emit(
                    state,
                    f"[WHY_THIS_STEP] pass={pass_index} iterative target execution with context-aware mutation policy"
                )
                pass_actions = 0
                for idx, target in pending_targets:
                    file_index = DevMasterGraph._refresh_active_root_index(
                        state,
                        category="implementation_index_refresh",
                    )
                    try:
                        action, _resolved_target = execute_implementation_target(
                            state=state,
                            graph_cls=DevMasterGraph,
                            idx=idx,
                            target=target,
                            pass_index=pass_index,
                            scope_root=scope_root,
                            project_root=project_root,
                            active_root=active_root,
                            file_index=file_index,
                            target_proofs=target_proofs,
                        )
                    except Exception as target_error:
                        item_id = f"todo_impl_{idx}"
                        if item_id not in failed_target_ids:
                            failed_target_ids.append(item_id)
                        state["errors"].append(f"[IMPLEMENTATION_TARGET_ERROR] {item_id}: {target_error}")
                        DevMasterGraph._set_checklist_status(
                            state,
                            item_id,
                            "failed",
                            evidence={
                                "phase": "implementation",
                                "pass_index": pass_index,
                                "error": str(target_error),
                            },
                        )
                        DevMasterGraph._emit_event(
                            state,
                            "implementation_target_failed",
                            target_id=item_id,
                            pass_index=pass_index,
                            error=str(target_error),
                        )
                        continue
                    DevMasterGraph._refresh_active_root_index(
                        state,
                        category="post_file_mutation_index_refresh",
                    )
                    DevMasterGraph._remember(
                        state,
                        "correction_attempts",
                        {
                            "pass_index": pass_index,
                            "target_index": idx,
                            "action": action,
                        },
                    )
                    pass_actions += 1
                    total_actions += 1
                state["implementation_pass_statuses"].append(f"{pass_label}:completed:{pass_actions}")
                DevMasterGraph._emit(
                    state,
                    f"[PASS_SUMMARY] {pass_label} actions={pass_actions} touched_total={len(state.get('touched_paths', []))}"
                )
            for idx, target in pending_targets:
                key = f"todo_impl_{idx}"
                if key in failed_target_ids:
                    continue
                proof = target_proofs.get(key, {})
                before_hash = str(proof.get("before_hash", ""))
                after_hash = str(proof.get("after_hash", ""))
                before_path = str(proof.get("before_path", ""))
                after_path = str(proof.get("after_path", ""))
                action = str(proof.get("action", ""))
                mod_type = str(target.get("modification_type", "")).strip().lower()
                if mod_type in {"verify", "inspect", "check"}:
                    DevMasterGraph._set_checklist_status(
                        state,
                        f"todo_impl_{idx}",
                        "completed",
                        evidence={"phase": "implementation", "mode": "verify", "target": key},
                    )
                    continue
                if before_hash == after_hash:
                    rename_like_proof = action == "renamed_file" and before_path and after_path and before_path != after_path
                    if rename_like_proof:
                        DevMasterGraph._set_checklist_status(
                            state,
                            f"todo_impl_{idx}",
                            "completed",
                            evidence={
                                "phase": "implementation",
                                "before_hash": before_hash,
                                "after_hash": after_hash,
                                "before_path": DevMasterGraph._relpath_safe(state, before_path),
                                "after_path": DevMasterGraph._relpath_safe(state, after_path),
                                "action": action,
                            },
                        )
                        continue
                    raise RuntimeError(
                        f"Target mutation proof missing for {key or f'implementation_target_{idx}'}: no content delta detected."
                    )
                DevMasterGraph._set_checklist_status(
                    state,
                    f"todo_impl_{idx}",
                    "completed",
                    evidence={
                        "phase": "implementation",
                        "before_hash": before_hash,
                        "after_hash": after_hash,
                    },
                )
            review_findings = DevMasterGraph._run_implementation_review(
                state=state,
                active_root=active_root,
                targets=targets,
            )
            DevMasterGraph._emit_event(
                state,
                "implementation_review",
                findings=review_findings,
                passed=not bool(review_findings),
            )
            if review_findings:
                raise RuntimeError("review gate failed: " + "; ".join(review_findings))
        except Exception as e:
            state["errors"].append(f"[IMPLEMENTATION_ERROR] {e}")
            DevMasterGraph._remember(
                state,
                "candidate_rejections",
                {
                    "reason": str(e),
                    "phase": "implementation",
                },
            )
            state["implementation_status"] = "blocked"
            state["phase_status"]["execute_implementation_phase"] = "failed"
            return state

        pending_or_failed = [
            item
            for item in state.get("internal_checklist", [])
            if isinstance(item, dict)
            and str(item.get("kind", "")) == "implementation"
            and str(item.get("status", "")) != "completed"
        ]
        state["implementation_status"] = "completed" if not pending_or_failed else "partial_progress"
        state["phase_status"]["execute_implementation_phase"] = "completed"
        DevMasterGraph._emit(state, f"[IMPLEMENTATION_SUMMARY] total_actions={total_actions}")
        return state
