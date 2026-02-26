from __future__ import annotations

import ast
import os
import re
from typing import Any, Dict, List

from shared.pathing import normalize_rel_path


_RE_FUNC = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)")
_RE_CLASS = re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)")
_RE_CONST_COMP = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\(",
)


def _extract_python_symbols(file_path: str) -> List[Dict[str, Any]]:
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
            symbols.append(
                {
                    "name": node.name,
                    "kind": "class",
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "end_line": int(getattr(node, "end_lineno", 0) or 0),
                }
            )
        elif isinstance(node, ast.FunctionDef):
            symbols.append(
                {
                    "name": node.name,
                    "kind": "function",
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "end_line": int(getattr(node, "end_lineno", 0) or 0),
                }
            )
        elif isinstance(node, ast.AsyncFunctionDef):
            symbols.append(
                {
                    "name": node.name,
                    "kind": "async_function",
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "end_line": int(getattr(node, "end_lineno", 0) or 0),
                }
            )
    symbols.sort(key=lambda item: (str(item.get("name", "")), int(item.get("line", 0))))
    return symbols


def _extract_js_like_symbols(file_path: str) -> List[Dict[str, Any]]:
    symbols: List[Dict[str, Any]] = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except Exception:
        return symbols
    for idx, line in enumerate(lines, start=1):
        for pattern, kind in (
            (_RE_FUNC, "function"),
            (_RE_CLASS, "class"),
            (_RE_CONST_COMP, "value"),
        ):
            match = pattern.search(line)
            if match:
                symbols.append({"name": match.group(1), "kind": kind, "line": idx, "end_line": idx})
    symbols.sort(key=lambda item: (str(item.get("name", "")), int(item.get("line", 0))))
    return symbols


def build_symbol_index(active_root: str, rel_files: List[str]) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    by_name: Dict[str, List[str]] = {}
    for rel in rel_files:
        rel_norm = normalize_rel_path(rel)
        abs_path = os.path.join(active_root, rel_norm.replace("/", os.sep))
        ext = os.path.splitext(rel_norm)[1].lower()
        language = "unknown"
        symbols: List[Dict[str, Any]] = []
        if ext == ".py":
            language = "python"
            symbols = _extract_python_symbols(abs_path)
        elif ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            language = "javascript"
            symbols = _extract_js_like_symbols(abs_path)
        if not symbols:
            continue
        files.append({"path": rel_norm, "language": language, "symbols": symbols})
        for symbol in symbols:
            name = str(symbol.get("name", "")).strip()
            if not name:
                continue
            by_name.setdefault(name, []).append(rel_norm)
    return {"files": files, "by_name": by_name}
