from __future__ import annotations

from typing import Any, Dict, List


def rank_candidates_with_ai_fallback(
    *,
    rel_files: List[str],
    entrypoints: List[Dict[str, Any]],
    toolchain: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Model-free fallback ranking used when advanced providers or stack-specific
    detectors are weak. This keeps runtime deterministic while preserving an
    AI-first extension point in the same output shape.
    """
    ranked: List[Dict[str, Any]] = []
    stack_names = {str(item.get("name", "")).strip().lower() for item in toolchain if isinstance(item, dict)}
    for item in entrypoints:
        path = str(item.get("path", "")).strip()
        if not path:
            continue
        base_score = float(item.get("score", 0.0))
        if "android" in stack_names and "/app/src/main/" in path.replace("\\", "/").lower():
            base_score += 0.08
        if "ios" in stack_names and path.lower().endswith("appdelegate.swift"):
            base_score += 0.08
        ranked.append(
            {
                "path": path,
                "score": round(min(base_score, 0.99), 3),
                "reason": "ai_fallback_ranked",
            }
        )
    if ranked:
        ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
        return ranked[:30]

    # If entrypoints were not found, return generic likely candidates.
    heuristics: List[Dict[str, Any]] = []
    for rel in rel_files:
        low = rel.replace("\\", "/").lower()
        if low.endswith(("main.py", "main.ts", "main.tsx", "main.kt", "main.java", "program.cs")):
            heuristics.append({"path": rel, "score": 0.55, "reason": "ai_fallback_generic_entrypoint"})
        if "android" in stack_names and "androidmanifest.xml" in low:
            heuristics.append({"path": rel, "score": 0.56, "reason": "ai_fallback_android_manifest"})
        if "android" in stack_names and low.endswith("mainactivity.kt"):
            heuristics.append({"path": rel, "score": 0.58, "reason": "ai_fallback_android_activity"})
        if "ios" in stack_names and low.endswith("appdelegate.swift"):
            heuristics.append({"path": rel, "score": 0.58, "reason": "ai_fallback_ios_delegate"})
    heuristics.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
    return heuristics[:30]
