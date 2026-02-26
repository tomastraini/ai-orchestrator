from __future__ import annotations

import os
import re
from typing import Tuple


def patch_region(original: str, replacement: str) -> Tuple[str, bool]:
    if replacement == original:
        return original, False
    return replacement, True


def replace_symbol(original: str, symbol: str, replacement: str) -> Tuple[str, bool]:
    if not symbol or symbol not in original:
        return original, False
    updated = original.replace(symbol, replacement, 1)
    return updated, updated != original


def insert_after_symbol(original: str, symbol: str, addition: str) -> Tuple[str, bool]:
    if not symbol or symbol not in original:
        return original, False
    idx = original.find(symbol) + len(symbol)
    updated = original[:idx] + addition + original[idx:]
    return updated, updated != original


def update_imports(original: str, module: str, statement: str) -> Tuple[str, bool]:
    """
    Generic import update:
    - if an import for module already exists, replace first match with statement
    - otherwise prepend statement at top of file
    """
    clean_module = str(module or "").strip()
    clean_statement = str(statement or "").strip()
    if not clean_statement:
        return original, False
    lines = original.splitlines()
    if clean_module:
        pattern = re.compile(
            rf"^\s*(?:from\s+['\"]{re.escape(clean_module)}['\"]\s+import|import\s+.*['\"]{re.escape(clean_module)}['\"]).*"
        )
        for idx, line in enumerate(lines):
            if pattern.match(line):
                lines[idx] = clean_statement
                updated = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
                return updated, updated != original
    prefix = clean_statement + "\n"
    updated = prefix + original
    return updated, updated != original


def rename_path(*, src_path: str, dest_path: str) -> Tuple[bool, str]:
    src = os.path.abspath(src_path)
    dst = os.path.abspath(dest_path)
    if src == dst:
        return False, "noop_same_path"
    if not os.path.exists(src):
        return False, "source_missing"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)
    return True, "renamed"

