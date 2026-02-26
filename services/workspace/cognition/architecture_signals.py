from __future__ import annotations

from typing import Any, Dict, List


def detect_architecture_signals(rel_files: List[str]) -> List[Dict[str, Any]]:
    files = [str(x).replace("\\", "/").casefold() for x in rel_files]
    has_features = any("/features/" in f for f in files)
    has_layers = any("/domain/" in f for f in files) and any("/infrastructure/" in f for f in files)
    has_mvc = any("/models/" in f for f in files) and any("/views/" in f for f in files)
    signals: List[Dict[str, Any]] = []
    if has_features:
        signals.append({"name": "feature_based", "confidence": 0.75})
    if has_layers:
        signals.append({"name": "layered", "confidence": 0.7})
    if has_mvc:
        signals.append({"name": "mvc", "confidence": 0.65})
    if not signals:
        signals.append({"name": "unknown", "confidence": 0.4})
    return signals
