"""Stream event types from Codex app-server."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    # Thread lifecycle
    THREAD_STARTED = "thread/started"
    THREAD_TOKEN_USAGE = "thread/tokenUsageUpdated"

    # Turn lifecycle
    TURN_STARTED = "turn/started"
    TURN_COMPLETED = "turn/completed"
    TURN_DIFF_UPDATED = "turn/diffUpdated"
    TURN_PLAN_UPDATED = "turn/planUpdated"

    # Agent messages
    AGENT_MESSAGE_DELTA = "item/agentMessage/delta"
    ITEM_STARTED = "item/started"
    ITEM_COMPLETED = "item/completed"

    # Command execution
    COMMAND_EXEC_OUTPUT = "item/commandExecution/outputDelta"
    TERMINAL_INTERACTION = "terminalInteraction"

    # File changes
    FILE_CHANGE_OUTPUT = "item/fileChange/outputDelta"

    # Reasoning
    REASONING_TEXT_DELTA = "item/reasoning/textDelta"
    REASONING_SUMMARY_DELTA = "item/reasoningSummary/textDelta"
    REASONING_SUMMARY_ADDED = "item/reasoningSummary/partAdded"

    # MCP
    MCP_TOOL_PROGRESS = "item/mcpToolCall/progress"

    # Approval requests (server -> client requests, not notifications)
    COMMAND_APPROVAL = "item/commandExecution/requestApproval"
    FILE_CHANGE_APPROVAL = "item/fileChange/requestApproval"
    TOOL_USER_INPUT = "item/toolRequestUserInput"

    # Config / account
    CONFIG_WARNING = "config/warning"
    ACCOUNT_UPDATED = "account/updated"
    ACCOUNT_RATE_LIMITS = "account/rateLimitsUpdated"
    CONTEXT_COMPACTED = "context/compacted"
    ERROR = "error"
    DEPRECATION = "deprecationNotice"
    RAW_RESPONSE_COMPLETED = "rawResponseItem/completed"

    UNKNOWN = "_unknown"


@dataclass
class StreamEvent:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    request_id: int | str | None = None  # set if this is a server request (approval)

    @property
    def event_type(self) -> EventType:
        try:
            return EventType(self.method)
        except ValueError:
            return EventType.UNKNOWN

    @property
    def text_delta(self) -> str | None:
        if self.method == EventType.AGENT_MESSAGE_DELTA:
            return self.params.get("delta") or self.params.get("textDelta")
        return None

    @property
    def reasoning_delta(self) -> str | None:
        if self.method in (EventType.REASONING_TEXT_DELTA, EventType.REASONING_SUMMARY_DELTA):
            return self.params.get("textDelta") or self.params.get("delta")
        return None

    @property
    def command_output(self) -> str | None:
        if self.method == EventType.COMMAND_EXEC_OUTPUT:
            return self.params.get("delta")
        return None

    @property
    def is_turn_completed(self) -> bool:
        return self.method == EventType.TURN_COMPLETED

    @property
    def is_thread_started(self) -> bool:
        return self.method == EventType.THREAD_STARTED

    @property
    def is_approval_request(self) -> bool:
        return self.method in (
            EventType.COMMAND_APPROVAL,
            EventType.FILE_CHANGE_APPROVAL,
            EventType.TOOL_USER_INPUT,
        )

    @property
    def thread_id(self) -> str | None:
        return self.params.get("threadId")

    @property
    def item_id(self) -> str | None:
        return self.params.get("itemId")
