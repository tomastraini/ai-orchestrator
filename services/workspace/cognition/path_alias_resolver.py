from __future__ import annotations

import json
import os
from typing import Any, Dict, List


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def detect_path_aliases(active_root: str) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    tsconfig = os.path.join(active_root, "tsconfig.json")
    if os.path.isfile(tsconfig):
        cfg = _read_json(tsconfig)
        compiler_options = cfg.get("compilerOptions", {})
        paths = compiler_options.get("paths", {}) if isinstance(compiler_options, dict) else {}
        if isinstance(paths, dict):
            for key, val in paths.items():
                if not isinstance(val, list) or not val:
                    continue
                clean_key = str(key).replace("/*", "").strip()
                clean_value = str(val[0]).replace("/*", "").strip()
                if clean_key:
                    aliases[clean_key] = clean_value
    return aliases


def build_entrypoint_aliases(rel_files: List[str]) -> Dict[str, List[str]]:
    by_dir: Dict[str, List[str]] = {}
    for rel in rel_files:
        norm = str(rel).replace("\\", "/")
        base = os.path.basename(norm).lower()
        if not (base.startswith("index.") or base.startswith("main.") or base.startswith("app.")):
            continue
        parent = os.path.dirname(norm)
        by_dir.setdefault(parent, []).append(norm)
    return by_dir
