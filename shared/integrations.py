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


class PMQuestionDispatcher(Protocol):
    def send_questions(
        self,
        *,
        request_id: str,
        questions: list[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Dispatch PM clarification questions (future: Teams/email/chat integrations).
        """
        raise NotImplementedError


class ApprovalSource(Protocol):
    def await_approval(
        self,
        *,
        request_id: str,
        plan_summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Obtain plan approval from user/system source (future: Teams/Jira).
        """
        raise NotImplementedError


class DevCompletionPublisher(Protocol):
    def publish_dev_completion(
        self,
        *,
        request_id: str,
        status: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Publish completion event for downstream PR/Jira workflows.
        """
        raise NotImplementedError


class PMOutcomePublisher(Protocol):
    def publish_pm_outcome(
        self,
        *,
        request_id: str,
        summary: str,
        plan: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Publish finalized PM outputs for downstream consumers.
        """
        raise NotImplementedError


class DevHandoffPublisher(Protocol):
    def publish_dev_handoff(
        self,
        *,
        request_id: str,
        handoff: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Publish generated PM->Dev handoff artifacts.
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


class NullPMQuestionDispatcher:
    def send_questions(
        self,
        *,
        request_id: str,
        questions: list[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        # No-op for local CLI-only runs.
        _ = (request_id, questions, metadata)


class NullApprovalSource:
    def await_approval(
        self,
        *,
        request_id: str,
        plan_summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        # Local fallback defaults to approval; orchestrator CLI remains source of truth.
        _ = (request_id, plan_summary, metadata)
        return True


class NullDevCompletionPublisher:
    def publish_dev_completion(
        self,
        *,
        request_id: str,
        status: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        # No-op for local CLI-only runs.
        _ = (request_id, status, summary, metadata)


class NullPMOutcomePublisher:
    def publish_pm_outcome(
        self,
        *,
        request_id: str,
        summary: str,
        plan: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        # No-op for local CLI-only runs.
        _ = (request_id, summary, plan, metadata)


class NullDevHandoffPublisher:
    def publish_dev_handoff(
        self,
        *,
        request_id: str,
        handoff: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        # No-op for local CLI-only runs.
        _ = (request_id, handoff, metadata)
