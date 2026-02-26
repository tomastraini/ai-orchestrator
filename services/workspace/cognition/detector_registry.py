from __future__ import annotations

import importlib.util
from typing import Dict


def _has_module(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def detect_provider_capabilities() -> Dict[str, bool]:
    """
    Report optional provider availability. Core cognition must work
    even when all of these are unavailable.
    """
    return {
        "tree_sitter": _has_module("tree_sitter"),
        "tree_sitter_languages": _has_module("tree_sitter_languages"),
        "libcst": _has_module("libcst"),
        "networkx": _has_module("networkx"),
        "yaml": _has_module("yaml"),
        "orjson": _has_module("orjson"),
    }
