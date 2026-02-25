from __future__ import annotations

import difflib
import os
import re
from typing import Any, Dict, List, Optional


def _tokenize(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9]+", text.lower()) if len(tok) > 1}


def is_vague_existing_project_request(requirement: str) -> bool:
    req = (requirement or "").lower()
    if not req.strip():
        return False
    if any(marker in req for marker in ["create ", "new project", "from scratch", "scaffold"]):
        return False
    return any(marker in req for marker in ["improve", "update", "enhance", "fix", "refactor"])


def _project_markers(project_path: str) -> List[str]:
    markers: List[str] = []
    if os.path.exists(os.path.join(project_path, "package.json")):
        markers.append("node")
    if os.path.exists(os.path.join(project_path, "nest-cli.json")):
        markers.append("nestjs")
    if os.path.exists(os.path.join(project_path, "tsconfig.json")):
        markers.append("typescript")
    return markers


def resolve_project_candidates(requirement: str, projects_root: str) -> List[Dict[str, Any]]:
    if not os.path.isdir(projects_root):
        return []
    req_tokens = _tokenize(requirement)
    rows: List[Dict[str, Any]] = []
    for entry in os.listdir(projects_root):
        project_path = os.path.join(projects_root, entry)
        if not os.path.isdir(project_path):
            continue
        name_tokens = _tokenize(entry.replace("-", " "))
        overlap = len(req_tokens.intersection(name_tokens))
        sim = difflib.SequenceMatcher(None, requirement.lower(), entry.lower()).ratio()
        markers = _project_markers(project_path)
        marker_bonus = 0.0
        req_low = requirement.lower()
        if "nest" in req_low and "nestjs" in markers:
            marker_bonus += 0.15
        if "typescript" in req_low and "typescript" in markers:
            marker_bonus += 0.1
        score = (overlap * 0.25) + (sim * 0.6) + marker_bonus
        rows.append(
            {
                "name": entry,
                "path_hint": f"projects/{entry}",
                "score": round(score, 4),
                "markers": markers,
            }
        )
    rows.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    return rows


def select_top_candidate(requirement: str, projects_root: str) -> Optional[Dict[str, Any]]:
    candidates = resolve_project_candidates(requirement, projects_root)
    if not candidates:
        return None
    top = candidates[0]
    if float(top.get("score", 0.0)) < 0.2:
        return None
    return top
