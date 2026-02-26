from __future__ import annotations

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

