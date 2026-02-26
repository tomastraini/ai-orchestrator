from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_store() -> Dict[str, Any]:
    return {"latest_session": None, "history": []}


class DevSessionStore:
    """
    JSON-backed store for cross-run DEV continuation sessions.
    """

    def __init__(self, repo_root: str, relative_store_path: str = ".orchestrator/dev_session.json"):
        self.repo_root = repo_root
        self.store_path = os.path.join(repo_root, relative_store_path)

    def _ensure_parent_dir(self) -> None:
        parent = os.path.dirname(self.store_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _read_all(self) -> Dict[str, Any]:
        if not os.path.exists(self.store_path):
            return _empty_store()
        with open(self.store_path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        if not raw:
            return _empty_store()
        try:
            payload = json.loads(raw)
        except Exception:
            # Corrupt session store should never block orchestration.
            return _empty_store()
        if not isinstance(payload, dict):
            return _empty_store()
        latest = payload.get("latest_session")
        history = payload.get("history")
        return {
            "latest_session": latest if isinstance(latest, dict) else None,
            "history": history if isinstance(history, list) else [],
        }

    def _write_all(self, payload: Dict[str, Any]) -> None:
        self._ensure_parent_dir()
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    def get_latest_session(self) -> Optional[Dict[str, Any]]:
        latest = self._read_all().get("latest_session")
        return dict(latest) if isinstance(latest, dict) else None

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        normalized = str(session_id or "").strip()
        if not normalized:
            return None
        payload = self._read_all()
        latest = payload.get("latest_session")
        if isinstance(latest, dict) and str(latest.get("session_id", "")) == normalized:
            return dict(latest)
        for entry in payload.get("history", []):
            if isinstance(entry, dict) and str(entry.get("session_id", "")) == normalized:
                return dict(entry)
        return None

    def create_session(self, *, root_requirement: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        created = _utc_now_iso()
        session = {
            "session_id": str(session_id or uuid.uuid4()),
            "root_requirement": str(root_requirement or "").strip(),
            "created_at": created,
            "updated_at": created,
            "status": "active",
            "run_chain": [],
            "session_changelog": [],
        }
        payload = self._read_all()
        latest = payload.get("latest_session")
        if isinstance(latest, dict):
            payload["history"] = [latest] + [x for x in payload.get("history", []) if isinstance(x, dict)]
            payload["history"] = payload["history"][:30]
        payload["latest_session"] = session
        self._write_all(payload)
        return dict(session)

    def upsert_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(session, dict):
            return {}
        session_id = str(session.get("session_id", "")).strip()
        if not session_id:
            return {}
        payload = self._read_all()
        normalized = dict(session)
        normalized["session_id"] = session_id
        normalized["updated_at"] = _utc_now_iso()
        latest = payload.get("latest_session")
        if isinstance(latest, dict) and str(latest.get("session_id", "")) == session_id:
            payload["latest_session"] = normalized
        else:
            replaced = False
            new_history: List[Dict[str, Any]] = []
            for entry in payload.get("history", []):
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("session_id", "")) == session_id:
                    new_history.append(normalized)
                    replaced = True
                else:
                    new_history.append(entry)
            if not replaced:
                new_history.insert(0, normalized)
            payload["history"] = new_history[:30]
            payload["latest_session"] = normalized
        self._write_all(payload)
        return normalized

    def append_run_entry(
        self,
        *,
        session_id: str,
        request_id: str,
        parent_request_id: Optional[str],
        iteration_index: int,
        trigger_type: str,
        user_delta_requirement: str,
        final_status: str,
        summary: str,
        touched_paths: List[str],
        pending_tasks: List[str],
        continuation_reason: str = "",
        prior_run_summary: str = "",
        carry_forward_memory: bool = True,
        workspace_snapshot_hash: str = "",
    ) -> Optional[Dict[str, Any]]:
        session = self.get_session(session_id)
        if not isinstance(session, dict):
            return None
        chain = session.get("run_chain")
        if not isinstance(chain, list):
            chain = []
        entry = {
            "request_id": str(request_id or "").strip(),
            "parent_request_id": str(parent_request_id or "").strip(),
            "iteration_index": int(max(0, iteration_index)),
            "trigger_type": str(trigger_type or "initial"),
            "user_delta_requirement": str(user_delta_requirement or "").strip(),
            "final_status": str(final_status or "").strip(),
            "summary": str(summary or "").strip(),
            "touched_paths": [str(x) for x in touched_paths if str(x).strip()],
            "pending_tasks": [str(x) for x in pending_tasks if str(x).strip()],
            "continuation_reason": str(continuation_reason or "").strip(),
            "prior_run_summary": str(prior_run_summary or "").strip(),
            "carry_forward_memory": bool(carry_forward_memory),
            "workspace_snapshot_hash": str(workspace_snapshot_hash or "").strip(),
            "recorded_at": _utc_now_iso(),
        }
        chain.append(entry)
        session["run_chain"] = chain[-200:]
        changelog = session.get("session_changelog")
        if not isinstance(changelog, list):
            changelog = []
        changelog.append(
            {
                "iteration_index": entry["iteration_index"],
                "request_id": entry["request_id"],
                "summary": entry["summary"],
                "status": entry["final_status"],
                "recorded_at": entry["recorded_at"],
            }
        )
        session["session_changelog"] = changelog[-200:]
        return self.upsert_session(session)

    def close_session(self, session_id: str, reason: str = "closed_by_user") -> Optional[Dict[str, Any]]:
        session = self.get_session(session_id)
        if not isinstance(session, dict):
            return None
        session["status"] = "closed"
        session["close_reason"] = str(reason or "closed_by_user")
        session["closed_at"] = _utc_now_iso()
        return self.upsert_session(session)
