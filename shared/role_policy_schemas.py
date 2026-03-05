from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Set


ROLE_TOOL_POLICY: Dict[str, Set[str]] = {
    "pm": {"read", "mcp_limited"},
    "planner": {"read", "mcp_limited"},
    "builder": {"read", "write", "shell", "mcp"},
    "validator": {"read", "shell", "browser", "mcp"},
    "finalizer": {"read", "write", "mcp_limited"},
    "test_fixer": {"read", "write", "shell", "mcp"},
    "dependency_fixer": {"read", "write", "shell", "mcp"},
    "docs_writer": {"read", "write"},
    "security_reviewer": {"read", "shell", "mcp"},
}


@dataclass(frozen=True)
class PolicyDecision:
    role: str
    capability: str
    allowed: bool
    reason: str


def decide_tool_access(role: str, capability: str) -> PolicyDecision:
    allowed_caps = ROLE_TOOL_POLICY.get(role, set())
    allowed = capability in allowed_caps
    reason = "allowed_by_policy" if allowed else "denied_by_policy"
    return PolicyDecision(role=role, capability=capability, allowed=allowed, reason=reason)
