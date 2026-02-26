from __future__ import annotations

import ast
import os
import re
from typing import Any, Dict, List

from shared.pathing import normalize_rel_path


_RE_IMPORT_FROM = re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]")
_RE_IMPORT = re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]")
_RE_REQUIRE = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _resolve_relative_import(source_rel: str, module: str) -> str:
    if not module.startswith("."):
        return module
    parent = os.path.dirname(source_rel).replace("\\", "/")
    combined = os.path.normpath(os.path.join(parent, module.replace("/", os.sep))).replace("\\", "/")
    return combined.lstrip("./")


def _parse_python_imports(file_path: str, source_rel: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            source = fh.read()
    except Exception:
        return out
    try:
        tree = ast.parse(source)
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = str(alias.name or "").strip()
                if mod:
                    out.append({"module": mod, "members": [], "is_external": not mod.startswith(".")})
        elif isinstance(node, ast.ImportFrom):
            mod = str(node.module or "").strip()
            level = int(getattr(node, "level", 0) or 0)
            if level > 0:
                mod = "." * level + mod
            if not mod:
                continue
            members = [str(a.name) for a in node.names if getattr(a, "name", None)]
            out.append({"module": _resolve_relative_import(source_rel, mod), "members": members, "is_external": not mod.startswith(".")})
    return out


def _parse_js_like_imports(file_path: str, source_rel: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except Exception:
        return out
    for line in lines:
        for regex in (_RE_IMPORT_FROM, _RE_IMPORT, _RE_REQUIRE):
            match = regex.search(line)
            if not match:
                continue
            module = str(match.group(1)).strip()
            if not module:
                continue
            resolved = _resolve_relative_import(source_rel, module)
            out.append({"module": resolved, "members": [], "is_external": not module.startswith(".")})
    return out


def build_import_graph(active_root: str, rel_files: List[str]) -> Dict[str, Any]:
    files_imports: Dict[str, List[Dict[str, Any]]] = {}
    edges: List[Dict[str, str]] = []
    nodes: List[str] = [normalize_rel_path(x) for x in rel_files]
    node_set = set(nodes)

    for rel in nodes:
        abs_path = os.path.join(active_root, rel.replace("/", os.sep))
        ext = os.path.splitext(rel)[1].lower()
        imports: List[Dict[str, Any]] = []
        if ext == ".py":
            imports = _parse_python_imports(abs_path, rel)
        elif ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            imports = _parse_js_like_imports(abs_path, rel)
        files_imports[rel] = imports
        for item in imports:
            module = str(item.get("module", "")).replace("\\", "/").strip()
            if not module:
                continue
            if module in node_set:
                edges.append({"from": rel, "to": module, "type": "imports"})
            elif not bool(item.get("is_external", True)):
                # Try extension fallback for local imports without extension.
                for ext_candidate in (".ts", ".tsx", ".js", ".jsx", ".py", "/index.ts", "/index.tsx", "/index.js", "/index.py"):
                    candidate = f"{module}{ext_candidate}" if not ext_candidate.startswith("/") else f"{module}{ext_candidate}"
                    candidate = candidate.replace("//", "/")
                    if candidate in node_set:
                        edges.append({"from": rel, "to": candidate, "type": "imports"})
                        break
    return {"nodes": nodes, "edges": edges, "imports_by_file": files_imports}
