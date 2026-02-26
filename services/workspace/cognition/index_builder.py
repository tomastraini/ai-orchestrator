from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from shared.pathing import normalize_rel_path

from services.workspace.cognition.ai_fallback import rank_candidates_with_ai_fallback
from services.workspace.cognition.architecture_signals import detect_architecture_signals
from services.workspace.cognition.config_toolchain_detector import detect_configs_and_toolchain
from services.workspace.cognition.dependency_detector import detect_dependency_graph
from services.workspace.cognition.detector_registry import detect_provider_capabilities
from services.workspace.cognition.entrypoint_detector import detect_entrypoints
from services.workspace.cognition.import_graph_builder import build_import_graph
from services.workspace.cognition.path_alias_resolver import build_entrypoint_aliases, detect_path_aliases
from services.workspace.cognition.symbol_extractor import build_symbol_index
from services.workspace.cognition.test_mapper import detect_tests_and_mappings


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_hash(rel_files: List[str]) -> str:
    blob = "\n".join(sorted(rel_files))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _infer_language(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".py"}:
        return "python"
    if ext in {".ts", ".tsx"}:
        return "typescript"
    if ext in {".js", ".jsx", ".mjs", ".cjs"}:
        return "javascript"
    if ext in {".java", ".kt", ".kts"}:
        return "jvm"
    if ext in {".cs"}:
        return "csharp"
    if ext in {".cpp", ".cc", ".cxx", ".hpp", ".h"}:
        return "cpp"
    if ext in {".go"}:
        return "go"
    if ext in {".rs"}:
        return "rust"
    if ext in {".cbl", ".cob"}:
        return "cobol"
    return "unknown"


def _infer_kind(path: str) -> str:
    low = path.casefold()
    if "/tests/" in f"/{low}" or low.endswith(".test.ts") or low.endswith(".spec.ts"):
        return "test"
    if low.endswith(".md"):
        return "doc"
    if low.endswith(".json") or low.endswith(".toml") or low.endswith(".yaml") or low.endswith(".yml"):
        return "config"
    return "source"


def _build_resolution_hints(
    *,
    rel_files: List[str],
    entrypoints: List[Dict[str, Any]],
    entrypoint_aliases: Dict[str, List[str]],
    toolchain: List[Dict[str, Any]],
) -> Dict[str, Any]:
    by_basename: Dict[str, List[str]] = {}
    for rel in rel_files:
        by_basename.setdefault(os.path.basename(rel).casefold(), []).append(rel)
    entrypoint_candidates = [
        {"path": str(item.get("path", "")), "score": float(item.get("score", 0.0))}
        for item in entrypoints
        if str(item.get("path", ""))
    ]
    ai_ranked_candidates = rank_candidates_with_ai_fallback(
        rel_files=rel_files,
        entrypoints=entrypoints,
        toolchain=toolchain,
    )
    return {
        "by_basename": by_basename,
        "entrypoint_candidates": entrypoint_candidates[:20],
        "entrypoint_aliases": entrypoint_aliases,
        "ai_ranked_candidates": ai_ranked_candidates,
    }


def build_cognition_index(active_root: str, rel_files: List[str]) -> Dict[str, Any]:
    rel_list = [normalize_rel_path(str(item)) for item in rel_files if str(item).strip()]
    providers = detect_provider_capabilities()
    symbol_index = build_symbol_index(active_root, rel_list)
    import_graph = build_import_graph(active_root, rel_list)
    dependency_graph = detect_dependency_graph(active_root)
    test_info = detect_tests_and_mappings(rel_list)
    config_info = detect_configs_and_toolchain(active_root, rel_list)
    path_aliases = detect_path_aliases(active_root)
    entrypoint_aliases = build_entrypoint_aliases(rel_list)
    entrypoints = detect_entrypoints(
        rel_files=rel_list,
        symbol_index=symbol_index,
        graph=import_graph,
        entrypoint_aliases=entrypoint_aliases,
    )
    architecture_signals = detect_architecture_signals(rel_list)

    imports_by_file = import_graph.get("imports_by_file", {})
    symbol_files = {str(x.get("path", "")): x for x in symbol_index.get("files", []) if isinstance(x, dict)}
    entrypoint_scores = {str(x.get("path", "")): float(x.get("score", 0.0)) for x in entrypoints}
    files_payload: List[Dict[str, Any]] = []
    for rel in rel_list:
        imports = imports_by_file.get(rel, []) if isinstance(imports_by_file, dict) else []
        relationships = [
            {"type": "imports", "target": str(item.get("module", ""))}
            for item in imports
            if str(item.get("module", ""))
        ]
        files_payload.append(
            {
                "path": rel,
                "kind": _infer_kind(rel),
                "language": _infer_language(rel),
                "symbols": symbol_files.get(rel, {}).get("symbols", []),
                "imports": imports,
                "exports": [],
                "relationships": relationships,
                "entrypoint_score": entrypoint_scores.get(rel, 0.0),
                "confidence": 0.9 if rel in symbol_files or rel in entrypoint_scores else 0.6,
            }
        )

    return {
        "version": "2.0",
        "generated_at": _utc_now_iso(),
        "project_root": normalize_rel_path(active_root),
        "workspace_hash": _workspace_hash(rel_list),
        "provider_capabilities": providers,
        "active_root": normalize_rel_path(active_root),
        "file_count": len(rel_list),
        "files": files_payload,
        "symbol_index": symbol_index,
        "graph": {"nodes": import_graph.get("nodes", []), "edges": import_graph.get("edges", [])},
        "dependency_graph": dependency_graph,
        "entrypoints": entrypoints,
        "entrypoint_aliases": entrypoint_aliases,
        "tests": test_info.get("tests", []),
        "test_mappings": test_info.get("test_mappings", []),
        "configs": config_info.get("configs", []),
        "toolchain": config_info.get("toolchain", []),
        "path_aliases": path_aliases,
        "architecture_signals": architecture_signals,
        "resolution_hints": _build_resolution_hints(
            rel_files=rel_list,
            entrypoints=entrypoints,
            entrypoint_aliases=entrypoint_aliases,
            toolchain=config_info.get("toolchain", []),
        ),
    }
