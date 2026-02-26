from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Optional

from services.dev.types.dev_graph_state import DevGraphState
from shared.dev_schemas import DevChecklistItem


def reindex_checklist(state: DevGraphState) -> None:
    checklist = state.get("internal_checklist", [])
    state["checklist_index"] = {
        str(item.get("id", "")): idx
        for idx, item in enumerate(checklist)
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }


def upsert_checklist_item(state: DevGraphState, item: DevChecklistItem) -> None:
    checklist = state.get("internal_checklist", [])
    index = state.get("checklist_index", {})
    payload = asdict(item)
    item_id = item.id
    if item_id in index:
        checklist[index[item_id]] = payload
    else:
        checklist.append(payload)
    state["internal_checklist"] = checklist
    reindex_checklist(state)


def find_checklist_item(state: DevGraphState, item_id: str) -> Optional[Dict[str, Any]]:
    idx = state.get("checklist_index", {}).get(item_id)
    if idx is None:
        return None
    checklist = state.get("internal_checklist", [])
    if idx < 0 or idx >= len(checklist):
        return None
    item = checklist[idx]
    return item if isinstance(item, dict) else None


def append_item_evidence(item: Dict[str, Any], evidence: Optional[Dict[str, Any]]) -> None:
    if not evidence:
        return
    current = item.get("evidence")
    if not isinstance(current, list):
        current = []
    current.append(evidence)
    item["evidence"] = current


def set_checklist_status(
    state: DevGraphState,
    item_id: str,
    status: str,
    *,
    evidence: Optional[Dict[str, Any]] = None,
) -> None:
    item = find_checklist_item(state, item_id)
    if not item:
        return
    item["status"] = status
    append_item_evidence(item, evidence)
    state["checklist_cursor"] = item_id


def next_actionable_checklist_item(state: DevGraphState) -> Optional[Dict[str, Any]]:
    checklist = state.get("internal_checklist", [])
    completed = {
        str(item.get("id", ""))
        for item in checklist
        if isinstance(item, dict) and str(item.get("status", "")) == "completed"
    }
    for item in checklist:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "pending"))
        if status in {"completed", "failed"}:
            continue
        deps = item.get("depends_on", [])
        if isinstance(deps, list) and any(str(dep) not in completed for dep in deps):
            continue
        return item
    return None


def all_mandatory_checklist_items_completed(state: DevGraphState) -> bool:
    for item in state.get("internal_checklist", []):
        if not isinstance(item, dict):
            continue
        if not bool(item.get("mandatory", True)):
            continue
        if str(item.get("status", "")) != "completed":
            return False
    return True


def build_internal_checklist(state: DevGraphState) -> None:
    handoff = state.get("handoff") or {}
    restored = handoff.get("internal_checklist")
    restored_by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(restored, list) and restored:
        for item in restored:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "")).strip()
            if not item_id:
                continue
            restored_by_id[item_id] = item

    checklist: list[dict[str, Any]] = []
    for task in state.get("bootstrap_tasks", []):
        item_id = f"todo_{task.id}"
        default_item = asdict(
            DevChecklistItem(
                id=item_id,
                kind="bootstrap",
                description=task.description,
                task_ref=task.id,
                success_criteria=["command exits with code 0"],
            )
        )
        checklist.append(restored_by_id.get(item_id, default_item))
    for idx, target in enumerate(state.get("implementation_targets", []), start=1):
        file_name = str(target.get("file_name", "")).strip() or f"target_{idx}"
        item_id = f"todo_impl_{idx}"
        default_item = asdict(
            DevChecklistItem(
                id=item_id,
                kind="implementation",
                description=f"implement {file_name}",
                target_ref=str(target.get("expected_path_hint", file_name)),
                success_criteria=["target file mutated with evidence"],
            )
        )
        checklist.append(restored_by_id.get(item_id, default_item))
    for task in state.get("validation_tasks", []):
        item_id = f"todo_{task.id}"
        default_item = asdict(
            DevChecklistItem(
                id=item_id,
                kind="validation",
                description=task.description,
                task_ref=task.id,
                success_criteria=["validation task completed"],
                mandatory=False,
            )
        )
        checklist.append(restored_by_id.get(item_id, default_item))
    for task in state.get("final_compile_tasks", []):
        deps = [str(item.get("id")) for item in checklist if isinstance(item, dict)]
        item_id = f"todo_{task.id}"
        default_item = asdict(
            DevChecklistItem(
                id=item_id,
                kind="final_compile",
                description=task.description,
                task_ref=task.id,
                depends_on=deps,
                success_criteria=["compile/build gate command completed"],
            )
        )
        checklist.append(restored_by_id.get(item_id, default_item))
    state["internal_checklist"] = checklist
    reindex_checklist(state)

