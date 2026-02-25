from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


@dataclass
class RequirementEnvelope:
    source_type: str
    source_id: Optional[str]
    initiator_id: Optional[str]
    requirement_text: str
    metadata: Dict[str, Any]


class RequirementSourceProvider(Protocol):
    def fetch_requirement(self, source_id: str) -> RequirementEnvelope:
        """
        Fetch a requirement from an external source (CLI/Jira/etc).
        """
        raise NotImplementedError


class WorkItemTracker(Protocol):
    def update_status(
        self,
        work_item_id: str,
        *,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update issue/task status in a provider (Jira/Azure DevOps/etc).
        """
        raise NotImplementedError


class PRNotifier(Protocol):
    def notify_pr_event(
        self,
        *,
        pr_url: str,
        title: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Publish PR event notifications (GitHub/Azure DevOps/Teams/etc).
        """
        raise NotImplementedError


class NullWorkItemTracker:
    def update_status(
        self,
        work_item_id: str,
        *,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        # No-op tracker for local CLI-only runs.
        _ = (work_item_id, status, metadata)


class NullPRNotifier:
    def notify_pr_event(
        self,
        *,
        pr_url: str,
        title: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        # No-op notifier for local CLI-only runs.
        _ = (pr_url, title, summary, metadata)
