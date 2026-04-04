"""
Human-in-the-loop (HITL) — approval gates, escalation, and intervention.

Three modes:
1. Approval gates — pause workflow and wait for human yes/no
2. Escalation — agent encounters something outside its scope, escalates to human
3. Intervention — human can inject messages into any agent thread at any time
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

from orchestrator.events.bus import EventBus

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"  # approved with changes
    TIMEOUT = "timeout"


class EscalationLevel(str, Enum):
    INFO = "info"           # FYI, no action needed
    QUESTION = "question"   # needs answer to continue
    APPROVAL = "approval"   # needs yes/no to proceed
    CRITICAL = "critical"   # blocked, cannot continue


@dataclass
class ApprovalRequest:
    request_id: str
    agent_id: str
    role: str
    workflow_run_id: str | None
    step_id: str | None
    action: str                     # what the agent wants to do
    context: str                    # details/reasoning
    risk_level: str = "low"         # low, medium, high, critical
    auto_approve_after: float | None = None  # seconds, None = wait forever
    status: ApprovalStatus = ApprovalStatus.PENDING
    response: str | None = None     # human's response text
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: str | None = None


@dataclass
class Escalation:
    escalation_id: str
    agent_id: str
    role: str
    level: EscalationLevel
    subject: str
    detail: str
    thread_id: str | None = None
    suggested_actions: list[str] = field(default_factory=list)
    status: str = "open"
    response: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class HITLManager:
    """
    Manages human-in-the-loop interactions.

    Approval requests and escalations are published to the event bus.
    The API server / CLI picks them up and presents them to the human.
    The human responds via the API, which resolves the future.
    """

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._pending_approvals: dict[str, asyncio.Future[ApprovalRequest]] = {}
        self._pending_escalations: dict[str, asyncio.Future[Escalation]] = {}
        self._approval_history: list[ApprovalRequest] = []
        self._escalation_history: list[Escalation] = []
        self._auto_rules: list[AutoApprovalRule] = []

    # ── Approval Gates ──────────────────────────────────────────────────

    async def request_approval(
        self,
        request_id: str,
        agent_id: str,
        role: str,
        action: str,
        context: str,
        risk_level: str = "low",
        workflow_run_id: str | None = None,
        step_id: str | None = None,
        timeout: float = 3600,
        auto_approve_after: float | None = None,
    ) -> ApprovalRequest:
        """
        Request human approval. Blocks until the human responds or timeout.

        Returns the resolved ApprovalRequest with status and response.
        """
        req = ApprovalRequest(
            request_id=request_id,
            agent_id=agent_id,
            role=role,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            action=action,
            context=context,
            risk_level=risk_level,
            auto_approve_after=auto_approve_after,
        )

        # Check auto-approval rules first
        for rule in self._auto_rules:
            if rule.matches(req):
                req.status = ApprovalStatus.APPROVED
                req.response = f"Auto-approved by rule: {rule.name}"
                req.resolved_at = datetime.now(timezone.utc).isoformat()
                self._approval_history.append(req)
                await self._bus.publish("hitl.approval.auto", {
                    "request_id": request_id, "rule": rule.name,
                }, source="hitl")
                return req

        # Create future and publish request
        future: asyncio.Future[ApprovalRequest] = asyncio.get_event_loop().create_future()
        self._pending_approvals[request_id] = future

        await self._bus.publish("hitl.approval.requested", {
            "request_id": request_id,
            "agent_id": agent_id,
            "role": role,
            "action": action,
            "context": context[:500],
            "risk_level": risk_level,
        }, source="hitl")

        # Handle auto-approve timeout
        effective_timeout = auto_approve_after or timeout

        try:
            if auto_approve_after:
                try:
                    return await asyncio.wait_for(future, timeout=auto_approve_after)
                except TimeoutError:
                    req.status = ApprovalStatus.APPROVED
                    req.response = f"Auto-approved after {auto_approve_after}s timeout"
                    req.resolved_at = datetime.now(timezone.utc).isoformat()
                    self._approval_history.append(req)
                    return req
            else:
                return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            req.status = ApprovalStatus.TIMEOUT
            req.resolved_at = datetime.now(timezone.utc).isoformat()
            self._approval_history.append(req)
            return req
        finally:
            self._pending_approvals.pop(request_id, None)

    def resolve_approval(
        self,
        request_id: str,
        status: ApprovalStatus,
        response: str | None = None,
    ):
        """Resolve a pending approval (called by API/CLI)."""
        future = self._pending_approvals.get(request_id)
        if not future or future.done():
            return

        req = ApprovalRequest(
            request_id=request_id,
            agent_id="",
            role="",
            workflow_run_id=None,
            step_id=None,
            action="",
            context="",
            status=status,
            response=response,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )
        future.set_result(req)
        self._approval_history.append(req)

    # ── Escalations ─────────────────────────────────────────────────────

    async def escalate(
        self,
        escalation_id: str,
        agent_id: str,
        role: str,
        level: EscalationLevel,
        subject: str,
        detail: str,
        thread_id: str | None = None,
        suggested_actions: list[str] | None = None,
        timeout: float = 3600,
    ) -> Escalation:
        """Escalate to human. Blocks until resolved or timeout."""
        esc = Escalation(
            escalation_id=escalation_id,
            agent_id=agent_id,
            role=role,
            level=level,
            subject=subject,
            detail=detail,
            thread_id=thread_id,
            suggested_actions=suggested_actions or [],
        )

        if level == EscalationLevel.INFO:
            # Info-level doesn't block
            esc.status = "noted"
            self._escalation_history.append(esc)
            await self._bus.publish("hitl.escalation.info", {
                "escalation_id": escalation_id,
                "subject": subject,
                "detail": detail[:500],
            }, source="hitl")
            return esc

        future: asyncio.Future[Escalation] = asyncio.get_event_loop().create_future()
        self._pending_escalations[escalation_id] = future

        await self._bus.publish("hitl.escalation.requested", {
            "escalation_id": escalation_id,
            "agent_id": agent_id,
            "role": role,
            "level": level.value,
            "subject": subject,
            "detail": detail[:500],
            "suggested_actions": suggested_actions or [],
        }, source="hitl")

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            esc.status = "timeout"
            self._escalation_history.append(esc)
            return esc
        finally:
            self._pending_escalations.pop(escalation_id, None)

    def resolve_escalation(
        self,
        escalation_id: str,
        response: str,
    ):
        """Resolve a pending escalation (called by API/CLI)."""
        future = self._pending_escalations.get(escalation_id)
        if not future or future.done():
            return

        esc = Escalation(
            escalation_id=escalation_id,
            agent_id="",
            role="",
            level=EscalationLevel.INFO,
            subject="",
            detail="",
            status="resolved",
            response=response,
        )
        future.set_result(esc)
        self._escalation_history.append(esc)

    # ── Auto-approval Rules ─────────────────────────────────────────────

    def add_auto_rule(self, rule: AutoApprovalRule):
        self._auto_rules.append(rule)

    # ── Status ──────────────────────────────────────────────────────────

    @property
    def pending_approvals(self) -> list[str]:
        return list(self._pending_approvals.keys())

    @property
    def pending_escalations(self) -> list[str]:
        return list(self._pending_escalations.keys())


@dataclass
class AutoApprovalRule:
    """Rule for auto-approving certain requests."""
    name: str
    roles: list[str] | None = None          # None = all roles
    risk_levels: list[str] | None = None    # None = all levels
    action_patterns: list[str] | None = None  # regex patterns on action string

    def matches(self, req: ApprovalRequest) -> bool:
        import re
        if self.roles and req.role not in self.roles:
            return False
        if self.risk_levels and req.risk_level not in self.risk_levels:
            return False
        if self.action_patterns:
            return any(re.search(p, req.action) for p in self.action_patterns)
        return True
