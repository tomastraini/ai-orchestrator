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


class TargetResolverMixin:
    @staticmethod
    def _compute_discovery_candidates(state: DevGraphState) -> List[Dict[str, Any]]:
        repo_root = os.path.dirname(str(state.get("scope_root", "")).rstrip(os.sep))
        requirement_text = " ".join(
            [
                str(state.get("plan", {}).get("summary", "")),
                " ".join([str(x) for x in state.get("plan", {}).get("constraints", []) if isinstance(x, str)]),
                " ".join([str(x) for x in state.get("plan", {}).get("validation", []) if isinstance(x, str)]),
            ]
        ).strip()
        ctx = scan_workspace_context(repo_root, file_limit=600)
        ranked = rank_candidate_files(requirement_text, ctx.get("sampled_files", []), top_k=80)
        state["dev_discovery_candidates"] = ranked
        DevMasterGraph._emit_event(
            state,
            "dev_discovery_ranked",
            requirement_excerpt=DevMasterGraph._sanitize_text(requirement_text, 300),
            candidate_count=len(ranked),
            top_candidates=ranked[:15],
        )
        return ranked

    @staticmethod
    def _is_within_scope(scope_root: str, candidate_path: str) -> bool:
        try:
            scope_abs = os.path.abspath(scope_root)
            cand_abs = os.path.abspath(candidate_path)
            return os.path.commonpath([scope_abs, cand_abs]) == scope_abs
        except Exception:
            return False

    @staticmethod
    def _has_project_marker(path: str) -> bool:
        if not os.path.isdir(path):
            return False
        try:
            names = set(os.listdir(path))
        except Exception:
            return False
        if any(marker in names for marker in DevMasterGraph.PROJECT_MARKER_FILES):
            return True
        return any(name.endswith(".csproj") or name.endswith(".sln") for name in names)

    @staticmethod
    def _source_hint_count(path: str) -> int:
        if not os.path.isdir(path):
            return 0
        count = 0
        for name in DevMasterGraph.SOURCE_DIR_HINTS:
            if os.path.isdir(os.path.join(path, name)):
                count += 1
        return count

    @staticmethod
    def _normalize_target_tail(expected_path_hint: str, project_name: str) -> str:
        normalized = (expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        if normalized.startswith("projects/"):
            parts = [p for p in normalized.split("/") if p]
            if len(parts) >= 3:
                if project_name and parts[1] != project_name:
                    return "/".join(parts[2:])
                return "/".join(parts[2:])
            return ""
        return normalized

    @staticmethod
    def _resolve_active_project_root_after_bootstrap(
        *,
        state: DevGraphState,
        attempt_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        scope_root = str(state.get("scope_root", "")).strip()
        project_root = str(state.get("project_root", "")).strip()
        project_name = str(state.get("project_name", "")).strip()
        active_root = str(state.get("active_project_root", "")).strip()
        if not scope_root:
            return {"selected_root": active_root, "confidence": 0, "candidates": [], "ambiguous": False}

        scope_abs = os.path.abspath(scope_root)
        expected_tails = [
            DevMasterGraph._normalize_target_tail(str(t.get("expected_path_hint", "")), project_name)
            for t in state.get("implementation_targets", [])
            if isinstance(t, dict)
        ]
        expected_tails = [x for x in expected_tails if x]

        score_map: Dict[str, int] = {}
        reasons_map: Dict[str, List[str]] = {}

        def _add_candidate(path: str, score: int, reason: str) -> None:
            if not path:
                return
            cand_abs = canonicalize_scope_path(scope_abs, path)
            if not DevMasterGraph._is_within_scope(scope_abs, cand_abs):
                return
            if not os.path.isdir(cand_abs):
                return
            score_map[cand_abs] = score_map.get(cand_abs, 0) + int(score)
            reasons_map.setdefault(cand_abs, []).append(reason)

        # Seed candidates from known roots/cwds.
        if active_root:
            _add_candidate(active_root, 25, "initial_active_root")
        if project_root:
            rel = project_root.split("/", 1)[1] if project_root.startswith("projects/") else project_root
            _add_candidate(os.path.join(scope_abs, rel), 15, "project_root_hint")

        # Parse stdout/stderr for path evidence.
        regexes = [
            r"Success!\s+Created\s+.+?\s+at\s+([^\r\n]+)",
            r"Scaffolding project in\s+([^\r\n]+)",
            r"Created project at\s+([^\r\n]+)",
            r"Project created at\s+([^\r\n]+)",
        ]
        for attempt in attempt_history:
            blob = f"{attempt.get('stdout', '')}\n{attempt.get('stderr', '')}"
            for pattern in regexes:
                for m in re.finditer(pattern, blob, flags=re.IGNORECASE):
                    candidate = m.group(1).strip().rstrip(".")
                    _add_candidate(candidate, 90, f"stdout_pattern:{pattern}")
            attempt_cwd = str(attempt.get("cwd", "")).strip()
            if attempt_cwd:
                _add_candidate(attempt_cwd, 12, "attempt_cwd")

        for touched in state.get("touched_paths", []):
            if isinstance(touched, str):
                _add_candidate(touched, 10, "touched_path")

        # Scan immediate and nested directories for marker evidence.
        search_roots: Set[str] = set()
        for base in list(score_map.keys()):
            search_roots.add(base)
            parent = os.path.dirname(base)
            if parent and DevMasterGraph._is_within_scope(scope_abs, parent):
                search_roots.add(parent)

        ignore_dirs = {"node_modules", ".git", ".venv", "__pycache__", "dist", "build", ".next", ".cache"}
        for search_root in list(search_roots):
            if not os.path.isdir(search_root):
                continue
            for root, dirs, _files in os.walk(search_root):
                dirs[:] = [d for d in dirs if d not in ignore_dirs]
                depth = os.path.relpath(root, search_root).count(os.sep)
                if depth > 3:
                    dirs[:] = []
                    continue
                if DevMasterGraph._has_project_marker(root):
                    _add_candidate(root, max(40, 70 - (depth * 10)), f"marker_depth:{depth}")
                hints = DevMasterGraph._source_hint_count(root)
                if hints:
                    _add_candidate(root, hints * 6, f"source_hints:{hints}")

        for candidate in list(score_map.keys()):
            base = os.path.basename(candidate.rstrip("/\\"))
            if project_name and base == project_name:
                _add_candidate(candidate, 8, "basename_matches_project")
            if expected_tails:
                matches = 0
                for tail in expected_tails:
                    if os.path.exists(os.path.join(candidate, tail)):
                        matches += 1
                if matches:
                    _add_candidate(candidate, matches * 20, f"target_tail_matches:{matches}")

        ranked = sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)
        candidates = [
            {"path": path, "score": score, "reasons": reasons_map.get(path, [])}
            for path, score in ranked
        ]
        if not candidates:
            return {
                "selected_root": active_root,
                "confidence": 0,
                "candidates": [],
                "ambiguous": False,
            }

        top = candidates[0]
        second_score = candidates[1]["score"] if len(candidates) > 1 else -999
        confidence = int(top["score"])
        ambiguous = len(candidates) > 1 and confidence < 60 and (top["score"] - second_score) < 10
        return {
            "selected_root": top["path"],
            "confidence": confidence,
            "candidates": candidates[:5],
            "ambiguous": ambiguous,
        }

    @staticmethod
    def _normalize_expected_suffix_for_active_root(expected_path_hint: str, active_root: str, project_root: str) -> str:
        expected = str(expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        if not expected:
            return ""
        parts = [p for p in expected.split("/") if p]
        if expected.startswith("projects/") and len(parts) >= 3:
            parts = parts[2:]
        active_parts = [p for p in str(active_root or "").replace("\\", "/").split("/") if p]
        project_parts = [p for p in str(project_root or "").replace("\\", "/").split("/") if p]
        if len(active_parts) >= 1 and len(project_parts) >= 1:
            if active_parts[-1].casefold() == project_parts[-1].casefold():
                pass
        # Deduplicate active root tail from expected suffix, e.g. active_root=.../frontend and suffix starts frontend/
        if active_parts:
            tail = active_parts[-1].casefold()
            if parts and parts[0].casefold() == tail:
                parts = parts[1:]
        return "/".join(parts)

    @staticmethod
    def _choose_best_index_candidate(
        *,
        index: Dict[str, Any],
        expected_suffix: str,
        file_name: str,
    ) -> str:
        suffix_map = index.get("by_suffix_casefold", {}) if isinstance(index.get("by_suffix_casefold"), dict) else {}
        by_basename = index.get("by_basename_casefold", {}) if isinstance(index.get("by_basename_casefold"), dict) else {}
        cognition = index.get("cognition", {}) if isinstance(index.get("cognition"), dict) else {}
        resolution_hints = cognition.get("resolution_hints", {}) if isinstance(cognition.get("resolution_hints"), dict) else {}
        entrypoint_aliases = cognition.get("entrypoint_aliases", {}) if isinstance(cognition.get("entrypoint_aliases"), dict) else {}
        entrypoint_candidates = cognition.get("entrypoints", []) if isinstance(cognition.get("entrypoints"), list) else []
        ai_ranked_candidates = resolution_hints.get("ai_ranked_candidates", []) if isinstance(resolution_hints, dict) else []
        expected_low = expected_suffix.casefold().strip("/")
        if expected_low and expected_low in suffix_map:
            return str(suffix_map[expected_low])
        leaf = os.path.basename(str(file_name or "").replace("\\", "/")).strip()
        if leaf:
            candidates = by_basename.get(leaf.casefold(), [])
            if isinstance(candidates, list) and candidates:
                if expected_low:
                    scored: List[Tuple[int, str]] = []
                    expected_parts = [p for p in expected_low.split("/") if p]
                    for rel in candidates:
                        rel_low = str(rel).casefold()
                        score = 0
                        for part in expected_parts[:-1]:
                            if f"/{part}/" in f"/{rel_low}/":
                                score += 1
                        scored.append((score, rel))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    return str(scored[0][1])
                return str(candidates[0])
            hinted = resolution_hints.get("by_basename", {}) if isinstance(resolution_hints, dict) else {}
            hinted_candidates = hinted.get(leaf.casefold(), []) if isinstance(hinted, dict) else []
            if isinstance(hinted_candidates, list) and hinted_candidates:
                return str(hinted_candidates[0])

        # Entrypoint alias recovery for common and unknown scaffold conventions.
        expected_parent = os.path.dirname(expected_low)
        expected_base = os.path.basename(expected_low)
        if expected_base.startswith("index.") or expected_base.startswith("main.") or expected_base.startswith("app."):
            sibling_candidates: List[str] = []
            if expected_parent in entrypoint_aliases and isinstance(entrypoint_aliases[expected_parent], list):
                sibling_candidates.extend([str(x) for x in entrypoint_aliases[expected_parent]])
            if not sibling_candidates:
                for item in entrypoint_candidates:
                    candidate_path = str(item.get("path", ""))
                    if not candidate_path:
                        continue
                    if expected_parent and os.path.dirname(candidate_path.casefold()) != expected_parent:
                        continue
                    sibling_candidates.append(candidate_path)
            if sibling_candidates:
                preferred = sorted(
                    sibling_candidates,
                    key=lambda x: float(
                        next(
                            (
                                item.get("score", 0.0)
                                for item in entrypoint_candidates
                                if str(item.get("path", "")) == x
                            ),
                            0.0,
                        )
                    ),
                    reverse=True,
                )
                return str(preferred[0])
        if isinstance(ai_ranked_candidates, list) and ai_ranked_candidates:
            return str(ai_ranked_candidates[0].get("path", ""))
        return ""

    @staticmethod
    def _resolve_target_file_path(
        *,
        scope_root: str,
        project_root: str,
        active_project_root: str,
        expected_path_hint: str,
        file_name: str,
    ) -> str:
        scope_abs = os.path.abspath(os.path.normpath(scope_root))
        expected_norm = (expected_path_hint or "").replace("\\", "/").strip()
        while expected_norm.startswith("./"):
            expected_norm = expected_norm[2:]
        project_root_norm = (project_root or "").replace("\\", "/").strip()
        while project_root_norm.startswith("./"):
            project_root_norm = project_root_norm[2:]
        file_name_norm = (file_name or "").strip()

        project_name = ""
        if project_root_norm.startswith("projects/"):
            parts = [p for p in project_root_norm.split("/") if p]
            if len(parts) >= 2:
                project_name = parts[1]

        if active_project_root:
            active_abs = canonicalize_scope_path(scope_abs, active_project_root)
            if os.path.commonpath([scope_abs, active_abs]) == scope_abs:
                base_root = active_abs
            else:
                raise RuntimeError(f"Active project root escapes scope: {active_project_root}")
        else:
            rel = project_root_norm.split("/", 1)[1] if project_root_norm.startswith("projects/") else project_root_norm
            base_root = canonicalize_scope_path(scope_abs, os.path.join(scope_abs, rel))

        file_name_norm = file_name_norm.replace("\\", "/").strip()
        while file_name_norm.startswith("./"):
            file_name_norm = file_name_norm[2:]
        file_leaf = os.path.basename(file_name_norm) if file_name_norm else ""
        rel_path = expected_norm
        if expected_norm.startswith("projects/"):
            parts = [p for p in expected_norm.split("/") if p]
            if len(parts) >= 3:
                # If PM project name drifted, still anchor to active root.
                if project_name and parts[1] != project_name:
                    rel_path = "/".join(parts[2:])
                else:
                    rel_path = "/".join(parts[2:])
            elif len(parts) == 2:
                rel_path = file_name_norm or parts[-1]
            else:
                rel_path = file_name_norm
        elif not rel_path:
            rel_path = file_name_norm

        if not rel_path and file_leaf:
            rel_path = file_leaf

        expected_has_extension = bool(os.path.splitext(rel_path)[1]) if rel_path else False
        if rel_path and not expected_has_extension and file_leaf:
            rel_parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
            if not rel_parts or rel_parts[-1] != file_leaf:
                rel_path = "/".join(rel_parts + [file_leaf]) if rel_parts else file_leaf

        # If active root already points to a nested app root (frontend/backend/src),
        # remove duplicated leading segments from expected relative path.
        active_tail = os.path.basename(base_root.rstrip("/\\")).casefold()
        rel_parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
        if active_tail and rel_parts and rel_parts[0].casefold() == active_tail:
            rel_path = "/".join(rel_parts[1:])

        safe_path = canonicalize_scope_path(scope_abs, os.path.join(base_root, rel_path))
        if os.path.commonpath([scope_abs, safe_path]) != scope_abs:
            raise RuntimeError(f"Implementation target escapes scope: {expected_path_hint}")
        return safe_path

    @staticmethod
    def _discover_existing_path(
        active_root: str,
        expected_path_hint: str,
        file_name: str,
        *,
        project_root: str = "",
        file_index: Optional[Dict[str, Any]] = None,
        state: Optional[DevGraphState] = None,
    ) -> str:
        if not active_root or not os.path.isdir(active_root):
            return ""
        index = file_index or DevMasterGraph._build_active_root_file_index(active_root)
        expected_suffix = DevMasterGraph._normalize_expected_suffix_for_active_root(
            expected_path_hint,
            active_root,
            project_root,
        )
        probe_files = []
        scaffold_probe = index.get("scaffold_probe", {}) if isinstance(index.get("scaffold_probe"), dict) else {}
        if isinstance(scaffold_probe.get("files"), list):
            probe_files = [str(x) for x in scaffold_probe.get("files", [])]
        # Prefer scaffold-discovered concrete files for ambiguous paths.
        leaf = os.path.basename(str(file_name or "").replace("\\", "/")).strip().casefold()
        if leaf:
            by_leaf = [x for x in probe_files if os.path.basename(x).casefold() == leaf]
            if by_leaf:
                chosen = by_leaf[0]
                if state is not None:
                    key = expected_path_hint or file_name
                    evidence = state.setdefault("target_resolution_evidence", {})
                    evidence[key] = {
                        "resolved_path": chosen,
                        "resolution_method": "scaffold_probe",
                        "confidence": 0.9,
                        "candidates_considered": by_leaf[:8],
                    }
                return os.path.join(active_root, chosen)
        rel = DevMasterGraph._choose_best_index_candidate(
            index=index,
            expected_suffix=expected_suffix,
            file_name=file_name,
        )
        diagnostics_refs: List[str] = []
        if state is not None:
            memory = DevMasterGraph._ensure_repository_memory(state)
            refs = memory.get("diagnostic_file_refs", [])
            if isinstance(refs, list):
                diagnostics_refs = [str(x).replace("\\", "/").strip().casefold() for x in refs if str(x).strip()]
        if rel and state is not None and DevMasterGraph._has_recent_candidate_rejection(state, rel):
            rel = ""
        if not rel:
            by_basename = index.get("by_basename_casefold", {}) if isinstance(index.get("by_basename_casefold"), dict) else {}
            leaf = os.path.basename(str(file_name or "").replace("\\", "/")).strip().casefold()
            alternatives = list(by_basename.get(leaf, [])) if leaf else []
            if alternatives:
                boosted = sorted(
                    alternatives,
                    key=lambda candidate: (
                        1 if any(os.path.basename(candidate).casefold() in ref for ref in diagnostics_refs) else 0,
                        1 if expected_suffix and str(candidate).casefold().endswith(expected_suffix.casefold()) else 0,
                    ),
                    reverse=True,
                )
                for candidate in boosted:
                    if state is not None and DevMasterGraph._has_recent_candidate_rejection(state, candidate):
                        continue
                    rel = str(candidate)
                    break
        if not rel:
            if state is not None:
                key = expected_path_hint or file_name
                evidence = state.setdefault("target_resolution_evidence", {})
                evidence[key] = {
                    "resolved_path": "",
                    "resolution_method": "none",
                    "confidence": 0.0,
                    "candidates_considered": [],
                }
            return ""
        if state is not None:
            key = expected_path_hint or file_name
            confidence = 0.85 if os.path.basename(expected_suffix).casefold() == os.path.basename(rel).casefold() else 0.72
            evidence = state.setdefault("target_resolution_evidence", {})
            evidence[key] = {
                "resolved_path": rel,
                "resolution_method": "index_candidates",
                "confidence": confidence,
                "candidates_considered": [rel],
            }
            DevMasterGraph._remember(
                state,
                "candidate_attempts",
                {
                    "expected_path_hint": expected_path_hint,
                    "file_name": file_name,
                    "candidate_path": rel,
                    "confidence": confidence,
                    "resolution_method": evidence[key].get("resolution_method", ""),
                },
            )
        return os.path.join(active_root, rel)
