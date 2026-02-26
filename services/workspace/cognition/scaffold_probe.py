from __future__ import annotations

import os
from typing import Any, Dict, List

from shared.pathing import normalize_rel_path


PROBE_IGNORE_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".cursor", ".orchestrator"}


def probe_scaffold_layout(active_root: str, *, limit: int = 1200) -> Dict[str, Any]:
    files: List[str] = []
    top_level: List[str] = []
    if not active_root or not os.path.isdir(active_root):
        return {"active_root": normalize_rel_path(active_root), "files": [], "top_level": []}
    try:
        top_level = sorted([x for x in os.listdir(active_root) if not x.startswith(".")])[:80]
    except Exception:
        top_level = []
    for root, dirs, names in os.walk(active_root):
        dirs[:] = [d for d in dirs if d not in PROBE_IGNORE_DIRS]
        for name in names:
            rel = normalize_rel_path(os.path.relpath(os.path.join(root, name), active_root))
            files.append(rel)
            if len(files) >= limit:
                break
        if len(files) >= limit:
            break
    return {
        "active_root": normalize_rel_path(active_root),
        "files": files,
        "top_level": top_level,
    }
