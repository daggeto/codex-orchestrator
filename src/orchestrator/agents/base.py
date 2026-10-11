"""Abstract base agent and shared types."""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from functools import wraps
from typing import Any, AsyncIterator, Callable

from orchestrator.protocol.events import StreamEvent
from orchestrator.roles.loader import RoleConfig

logger = logging.getLogger(__name__)


def observed_stream(method):
    """Observe turns without awaiting observers or changing backend events."""
    @wraps(method)
    async def wrapped(self, thread_id, message, model=None):
        self._begin_activity(thread_id, message)
        stream = method(self, thread_id, message, model)
        completed = False
        try:
            async for event in stream:
                self._observe_event(thread_id, event)
                completed = completed or event.is_turn_completed
                yield event
            if not completed:
                self._activity_error = "Turn ended without completion (possibly timed out)"
        except asyncio.CancelledError:
            raise
        except GeneratorExit:
            raise
        except Exception as exc:
            self._activity_error = str(exc)
            raise
        finally:
            try:
                await stream.aclose()
            finally:
                self._active_turns.pop(thread_id, None)
                self._notify_observer("agent.activity", {"activity": self.activity})
    return wrapped


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
        self._active_turns: dict[str, dict[str, Any]] = {}
        self._activity_error: str | None = None
        self._observer: Callable[[str, dict[str, Any]], None] | None = None
        self._approval_policy = ApprovalPolicy(
            role.approval_policy or "on-failure"
        )

    @property
    def name(self) -> str:
        return f"{self.role.name}:{self.agent_id[:8]}"

    @property
    def activity(self) -> dict[str, Any]:
        return {
            "state": "working" if self._active_turns else (
                "error" if self._activity_error else "idle"),
            "threads": [dict(turn) for turn in self._active_turns.values()],
            "error": self._activity_error,
        }

    def set_observer(self, observer: Callable[[str, dict[str, Any]], None] | None):
        self._observer = observer

    def _notify_observer(self, topic: str, payload: dict[str, Any]):
        try:
            if self._observer:
                self._observer(topic, {"agent_id": self.agent_id, **payload})
        except Exception:
            logger.debug("Activity observer failed", exc_info=True)

    def _begin_activity(self, thread_id: str, message: str):
        thread = self._get_thread(thread_id)
        self._activity_error = None
        self._active_turns[thread_id] = {
            "thread_id": thread_id, "workspace": thread.cwd if thread else None,
            "message_preview": message[:240],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._notify_observer("agent.activity", {"activity": self.activity})

    def _observe_event(self, thread_id: str, event: StreamEvent):
        turn = event.params.get("turn", {})
        if not isinstance(turn, dict):
            turn = {}
        if event.method == "error" or event.params.get("is_error") or (
            turn.get("error") or turn.get("status") == "failed"
        ):
            self._activity_error = str(event.params.get("message") or
                                       turn.get("error") or "Backend turn error")
        self._notify_observer("agent.event", {
            "thread_id": thread_id, "method": event.method, "params": event.params,
        })

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
