# shared/state.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PipelineState:
    requirement: str
    request_id: Optional[str] = None
    source_type: str = "cli"
    source_id: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None

    branch_name: Optional[str] = None
    dev_status: str = "pending"
    build_logs: Optional[str] = None
    session_id: Optional[str] = None
    continuation_mode: str = "off"
    continue_from_request_id: Optional[str] = None
    delta_requirement: Optional[str] = None

    pr_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
