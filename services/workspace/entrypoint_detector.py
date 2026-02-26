from __future__ import annotations

import os
from typing import Any, Dict, List


ENTRYPOINT_BASENAMES = {
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
}


def detect_entrypoints(active_root: str, rel_files: List[str], symbol_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_name = symbol_index.get("by_name", {}) if isinstance(symbol_index, dict) else {}
    scores: Dict[str, int] = {}
    for rel in rel_files:
        rel_norm = rel.replace("\\", "/")
        base = os.path.basename(rel_norm).lower()
        score = 0
        if base in ENTRYPOINT_BASENAMES:
            score += 70
        if rel_norm.lower().endswith("/main.py") or rel_norm.lower() == "main.py":
            score += 15
        if base.startswith("index.") or base.startswith("main."):
            score += 10
        if base == "app.py":
            score += 8
        if score <= 0:
            continue
        scores[rel_norm] = score

    main_symbols = by_name.get("main", []) if isinstance(by_name, dict) else []
    for rel in main_symbols if isinstance(main_symbols, list) else []:
        rel_norm = str(rel).replace("\\", "/")
        scores[rel_norm] = max(20, int(scores.get(rel_norm, 0)) + 20)

    out = [{"path": path, "score": int(score)} for path, score in scores.items()]
    out.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
    return out[:20]

