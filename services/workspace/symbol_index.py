from __future__ import annotations

import ast
import os
from typing import Any, Dict, List

from shared.pathing import normalize_rel_path


def extract_python_symbols(file_path: str) -> List[Dict[str, Any]]:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            source = fh.read()
    except Exception:
        return []
    try:
        tree = ast.parse(source)
    except Exception:
        return []
    symbols: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append({"name": node.name, "kind": "class", "line": int(getattr(node, "lineno", 0) or 0)})
        elif isinstance(node, ast.FunctionDef):
            symbols.append({"name": node.name, "kind": "function", "line": int(getattr(node, "lineno", 0) or 0)})
        elif isinstance(node, ast.AsyncFunctionDef):
            symbols.append({"name": node.name, "kind": "async_function", "line": int(getattr(node, "lineno", 0) or 0)})
    symbols.sort(key=lambda item: (str(item.get("name", "")), int(item.get("line", 0))))
    return symbols


def build_symbol_index(active_root: str, rel_files: List[str]) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    by_name: Dict[str, List[str]] = {}
    for rel in rel_files:
        abs_path = os.path.join(active_root, rel.replace("/", os.sep))
        ext = os.path.splitext(rel)[1].lower()
        symbols: List[Dict[str, Any]] = []
        if ext == ".py":
            symbols = extract_python_symbols(abs_path)
        if not symbols:
            continue
        entry = {"path": normalize_rel_path(rel), "language": "python", "symbols": symbols}
        files.append(entry)
        for symbol in symbols:
            name = str(symbol.get("name", "")).strip()
            if not name:
                continue
            by_name.setdefault(name, []).append(normalize_rel_path(rel))
    return {"files": files, "by_name": by_name}

