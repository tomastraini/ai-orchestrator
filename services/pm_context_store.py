from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PMContextStore:
    """
    JSON-backed store for request-scoped PM clarification context.
    """

    def __init__(self, repo_root: str, relative_store_path: str = ".orchestrator/pm_context.json"):
        self.repo_root = repo_root
        self.store_path = os.path.join(repo_root, relative_store_path)

    def _ensure_parent_dir(self) -> None:
        parent_dir = os.path.dirname(self.store_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    def _read_all(self) -> Dict[str, Any]:
        if not os.path.exists(self.store_path):
            return {"requests": {}}

        with open(self.store_path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
            if not raw:
                return {"requests": {}}
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {"requests": {}}
            if "requests" not in data or not isinstance(data["requests"], dict):
                data["requests"] = {}
            return data

    def _write_all(self, data: Dict[str, Any]) -> None:
        self._ensure_parent_dir()
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def load_context(self, request_id: str, original_requirement: Optional[str] = None) -> Dict[str, Any]:
        data = self._read_all()
        requests = data["requests"]
        entry = requests.get(request_id)
        if isinstance(entry, dict):
            return entry

        new_entry: Dict[str, Any] = {
            "request_id": request_id,
            "original_requirement": original_requirement or "",
            "rounds": [],
            "current_hypothesis": {},
            "final_plan": None,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }
        requests[request_id] = new_entry
        self._write_all(data)
        return new_entry

    def update_hypothesis(self, request_id: str, hypothesis: Dict[str, Any]) -> None:
        data = self._read_all()
        requests = data["requests"]
        entry = requests.get(request_id)
        if not isinstance(entry, dict):
            entry = self.load_context(request_id)
            data = self._read_all()
            requests = data["requests"]
            entry = requests.get(request_id)

        entry["current_hypothesis"] = hypothesis
        entry["updated_at"] = _utc_now_iso()
        self._write_all(data)

    def append_round(self, request_id: str, question: str, answer: str) -> None:
        data = self._read_all()
        requests = data["requests"]
        entry = requests.get(request_id)
        if not isinstance(entry, dict):
            entry = self.load_context(request_id)
            data = self._read_all()
            requests = data["requests"]
            entry = requests.get(request_id)

        rounds = entry.get("rounds")
        if not isinstance(rounds, list):
            rounds = []
            entry["rounds"] = rounds

        rounds.append(
            {
                "question": question,
                "answer": answer,
                "timestamp": _utc_now_iso(),
            }
        )
        entry["updated_at"] = _utc_now_iso()
        self._write_all(data)

    def save_final_plan(self, request_id: str, plan: Dict[str, Any]) -> None:
        data = self._read_all()
        requests = data["requests"]
        entry = requests.get(request_id)
        if not isinstance(entry, dict):
            entry = self.load_context(request_id)
            data = self._read_all()
            requests = data["requests"]
            entry = requests.get(request_id)

        entry["final_plan"] = plan
        entry["updated_at"] = _utc_now_iso()
        self._write_all(data)

    def clear_context(self, request_id: str) -> None:
        data = self._read_all()
        requests = data["requests"]
        if request_id in requests:
            del requests[request_id]
            self._write_all(data)
