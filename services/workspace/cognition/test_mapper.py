from __future__ import annotations

import os
from typing import Any, Dict, List

from shared.pathing import normalize_rel_path


def _is_test_path(rel: str) -> bool:
    low = rel.casefold()
    return (
        "/tests/" in f"/{low}"
        or "/__tests__/" in f"/{low}"
        or low.endswith(".test.ts")
        or low.endswith(".test.tsx")
        or low.endswith(".test.js")
        or low.endswith(".test.jsx")
        or low.endswith(".spec.ts")
        or low.endswith(".spec.tsx")
        or low.endswith(".spec.js")
        or low.endswith(".spec.jsx")
        or low.endswith("_test.py")
        or low.startswith("test_")
        or "/test_" in low
    )


def detect_tests_and_mappings(rel_files: List[str]) -> Dict[str, Any]:
    tests = [normalize_rel_path(path) for path in rel_files if _is_test_path(path)]
    mappings: List[Dict[str, str]] = []
    src_set = {normalize_rel_path(x) for x in rel_files}
    for test in tests:
        stem = os.path.basename(test).replace(".test", "").replace(".spec", "")
        stem = stem.replace("_test", "").replace("test_", "")
        for src in src_set:
            if src == test:
                continue
            if os.path.basename(src).startswith(stem) or stem.startswith(os.path.splitext(os.path.basename(src))[0]):
                mappings.append({"test": test, "source": src})
                break
    return {"tests": sorted(set(tests)), "test_mappings": mappings}
