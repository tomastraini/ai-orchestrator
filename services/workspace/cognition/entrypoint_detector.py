from __future__ import annotations

import os
from typing import Any, Dict, List


KNOWN_ENTRY_BASENAMES = {
    "main.py",
    "__main__.py",
    "app.py",
    "main.ts",
    "main.tsx",
    "main.js",
    "main.jsx",
    "index.ts",
    "index.tsx",
    "index.js",
    "index.jsx",
    "program.cs",
    "main.java",
    "main.kt",
}


def detect_entrypoints(
    *,
    rel_files: List[str],
    symbol_index: Dict[str, Any],
    graph: Dict[str, Any],
    entrypoint_aliases: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    by_name = symbol_index.get("by_name", {}) if isinstance(symbol_index, dict) else {}
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    incoming_count: Dict[str, int] = {}
    for edge in edges if isinstance(edges, list) else []:
        to_path = str(edge.get("to", "")).replace("\\", "/")
        if to_path:
            incoming_count[to_path] = incoming_count.get(to_path, 0) + 1
    scored: List[Dict[str, Any]] = []
    for rel in rel_files:
        rel_norm = rel.replace("\\", "/")
        base = os.path.basename(rel_norm).lower()
        score = 0.0
        reasons: List[str] = []
        if base in KNOWN_ENTRY_BASENAMES:
            score += 0.7
            reasons.append("known_entry_basename")
        if base.startswith("index.") or base.startswith("main.") or base.startswith("app."):
            score += 0.1
            reasons.append("entrypoint_like_name")
        incoming = incoming_count.get(rel_norm, 0)
        if incoming > 0:
            score += min(0.15, incoming * 0.03)
            reasons.append("import_graph")
        main_symbols = by_name.get("main", []) if isinstance(by_name, dict) else []
        if rel_norm in main_symbols:
            score += 0.15
            reasons.append("contains_main_symbol")
        if score > 0:
            scored.append({"path": rel_norm, "score": round(min(score, 0.99), 3), "reasons": reasons})

    for siblings in entrypoint_aliases.values():
        if len(siblings) <= 1:
            continue
        for item in scored:
            if item.get("path") in siblings:
                item["aliases"] = [x for x in siblings if x != item.get("path")]

    scored.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    return scored[:30]
