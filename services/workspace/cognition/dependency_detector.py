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


def detect_dependency_graph(active_root: str) -> Dict[str, Any]:
    dependencies: List[Dict[str, Any]] = []
    package_json = os.path.join(active_root, "package.json")
    if os.path.isfile(package_json):
        pkg = _read_json(package_json)
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            block = pkg.get(section, {})
            if isinstance(block, dict):
                for name, version in block.items():
                    dependencies.append(
                        {"name": str(name), "version": str(version), "source": "npm", "scope": section}
                    )
    pyproject = os.path.join(active_root, "pyproject.toml")
    if os.path.isfile(pyproject):
        dependencies.append({"name": "pyproject.toml", "version": "", "source": "python", "scope": "project"})
    requirements = os.path.join(active_root, "requirements.txt")
    if os.path.isfile(requirements):
        try:
            with open(requirements, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    cleaned = line.strip()
                    if not cleaned or cleaned.startswith("#"):
                        continue
                    dependencies.append({"name": cleaned.split("==")[0], "version": cleaned, "source": "python", "scope": "requirements"})
        except Exception:
            pass
    return {"dependencies": dependencies}
