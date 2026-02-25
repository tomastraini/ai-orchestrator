from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


DevStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class DevTask:
    id: str
    description: str
    command: Optional[str] = None
    cwd: Optional[str] = None
    kind: Literal["bootstrap", "implementation", "validation"] = "implementation"


@dataclass
class DevClarificationQuestion:
    id: str
    question: str
    reason: str


@dataclass
class DevExecutionState:
    request_id: str
    pm_plan: Dict[str, Any]
    project_name: str
    allowed_root: str
    status: DevStatus = "pending"
    current_step: str = "init"
    dev_tasks: List[DevTask] = field(default_factory=list)
    clarifications: List[Dict[str, str]] = field(default_factory=list)
    execution_logs: List[str] = field(default_factory=list)
    touched_paths: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 5
    last_error: Optional[str] = None
    attempt_history: List[Dict[str, Any]] = field(default_factory=list)
    final_summary: Optional[str] = None


def derive_project_name(pm_plan: Dict[str, Any]) -> str:
    project_ref = pm_plan.get("project_ref")
    if isinstance(project_ref, dict):
        name = project_ref.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return "default-project"

