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


class ContentGeneratorMixin:
    @staticmethod
    def _comment_for_path(path: str, text: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext in {".ts", ".tsx", ".js", ".jsx", ".java", ".c", ".cpp", ".go", ".rs"}:
            return f"// {text}\n"
        if ext in {".py", ".sh", ".rb", ".yml", ".yaml"}:
            return f"# {text}\n"
        return f"{text}\n"

    @staticmethod
    def _component_name_from_file(file_name: str) -> str:
        stem = os.path.splitext(os.path.basename(file_name))[0]
        cleaned = re.sub(r"[^A-Za-z0-9_]", "", stem)
        if not cleaned:
            return "GeneratedComponent"
        return cleaned[0].upper() + cleaned[1:]

    @staticmethod
    def _default_generated_content(path: str, file_name: str, details: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext in {".tsx", ".jsx"}:
            component_name = DevMasterGraph._component_name_from_file(file_name)
            return (
                f"export default function {component_name}() {{\n"
                f"  return <div>{details or 'Generated component'}</div>;\n"
                "}\n"
            )
        if ext in {".ts", ".js"}:
            return "export {};\n"
        if ext in {".py"}:
            return "def main() -> None:\n    pass\n\n\nif __name__ == '__main__':\n    main()\n"
        if ext == ".md":
            return f"# {file_name}\n\n{details or 'Generated content'}\n"
        return (details or "Generated content") + "\n"

    @staticmethod
    def _infer_rename_destination(*, safe_target: str, file_name: str, expected_path_hint: str, details: str) -> str:
        # Prefer explicit extension/path mention in details when available.
        detail_text = str(details or "").strip()
        match = re.search(r"\bto\s+([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)\b", detail_text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).replace("\\", "/").strip()
            leaf = os.path.basename(candidate)
            if leaf:
                return os.path.join(os.path.dirname(safe_target), leaf)

        # If expected path hint points to a file leaf, use it.
        expected = str(expected_path_hint or "").replace("\\", "/").strip().lstrip("./")
        expected_leaf = os.path.basename(expected)
        if expected_leaf and "." in expected_leaf and expected_leaf != os.path.basename(file_name):
            return os.path.join(os.path.dirname(safe_target), expected_leaf)

        # Fallback: if source is .ts and detail suggests tsx/jsx, map extension.
        src_leaf = os.path.basename(safe_target)
        if src_leaf.endswith(".ts") and ("tsx" in detail_text.lower() or "jsx" in detail_text.lower()):
            return os.path.join(os.path.dirname(safe_target), src_leaf[:-3] + ".tsx")
        return ""

    @staticmethod
    def _llm_generate_file_content(
        *,
        state: DevGraphState,
        target_path: str,
        file_name: str,
        details: str,
        existing_content: str,
    ) -> str:
        requirement = str(state.get("plan", {}).get("summary", ""))
        constraints = [str(x) for x in state.get("plan", {}).get("constraints", []) if isinstance(x, str)]
        validation = [str(x) for x in state.get("plan", {}).get("validation", []) if isinstance(x, str)]
        prompt = (
            "You are a senior software engineer. Generate file content for a single target file. "
            "Return ONLY the full file contents. No markdown fences.\n"
            f"Target path: {target_path}\n"
            f"File name: {file_name}\n"
            f"Task details: {details}\n"
            f"Requirement summary: {requirement}\n"
            f"Constraints: {constraints}\n"
            f"Validation expectations: {validation}\n"
            f"Existing content (may be empty):\n{existing_content[:6000]}"
        )
        try:
            from config import client  # lazy import to avoid hard dependency during tests
        except Exception:
            return DevMasterGraph._default_generated_content(target_path, file_name, details)
        if not hasattr(client, "responses"):
            return DevMasterGraph._default_generated_content(target_path, file_name, details)
        try:
            response = client.responses.create(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.1-codex-mini"),
                input=[{"role": "user", "content": prompt}],
            )
            for item in response.output:
                if item.type != "message":
                    continue
                for part in item.content:
                    if part.type == "output_text":
                        text = str(part.text or "").strip()
                        if text:
                            return text + ("" if text.endswith("\n") else "\n")
        except Exception:
            pass
        return DevMasterGraph._default_generated_content(target_path, file_name, details)
