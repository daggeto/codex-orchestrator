"""Abstract base agent and shared types."""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

from orchestrator.protocol.events import StreamEvent
from orchestrator.roles.loader import RoleConfig

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class HealthStatus:
    healthy: bool
    latency_ms: float | None = None
    message: str = ""
    checks: dict[str, bool] = field(default_factory=dict)


@dataclass
class ThreadInfo:
    thread_id: str
    agent_id: str
    cwd: str | None = None
    model: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    thread_id: str
    full_text: str
    events: list[StreamEvent]
    token_usage: dict[str, int] = field(default_factory=dict)
    files_changed: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ApprovalPolicy(str, Enum):
    AUTO_APPROVE = "auto-approve"
    ON_FAILURE = "on-failure"
    ON_REQUEST = "on-request"
    NEVER = "never"


class Agent(ABC):
    """Base class for all agent backends (Codex, Claude, etc.)."""

    def __init__(self, agent_id: str, role: RoleConfig):
        self.agent_id = agent_id
        self.role = role
        self.status = AgentStatus.STOPPED
        self._threads: dict[str, ThreadInfo] = {}
        self._approval_policy = ApprovalPolicy(
            role.approval_policy or "on-failure"
        )

    @property
    def name(self) -> str:
        return f"{self.role.name}:{self.agent_id[:8]}"

    @abstractmethod
    async def start(self) -> None:
        """Start the agent backend process/connection."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop the agent."""

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Check if the agent is healthy."""

    @abstractmethod
    async def start_thread(
        self,
        cwd: str | None = None,
        model: str | None = None,
        thread_id: str | None = None,
    ) -> ThreadInfo:
        """Start a new conversation thread."""

    @abstractmethod
    async def resume_thread(self, thread_id: str) -> ThreadInfo:
        """Resume an existing thread."""

    @abstractmethod
    async def send_message(
        self, thread_id: str, message: str, model: str | None = None
    ) -> TurnResult:
        """Send a message and collect the full response."""

    @abstractmethod
    async def stream_message(
        self, thread_id: str, message: str, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        """Send a message and stream events as they arrive."""

    @abstractmethod
    async def interrupt(self, thread_id: str) -> None:
        """Interrupt the current turn on a thread."""

    @abstractmethod
    async def list_threads(self) -> list[ThreadInfo]:
        """List all threads for this agent."""

    async def fork_thread(self, thread_id: str) -> ThreadInfo:
        """Fork an existing thread. Override if supported."""
        raise NotImplementedError(f"{type(self).__name__} does not support thread forking")

    async def archive_thread(self, thread_id: str) -> None:
        """Archive a thread. Override if supported."""
        raise NotImplementedError(f"{type(self).__name__} does not support thread archiving")

    def _register_thread(self, info: ThreadInfo):
        self._threads[info.thread_id] = info

    def _get_thread(self, thread_id: str) -> ThreadInfo | None:
        return self._threads.get(thread_id)
