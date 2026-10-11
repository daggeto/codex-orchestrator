"""Claude Code agent backed by a persistent subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from typing import Any, AsyncIterator

from orchestrator.agents.base import (
    Agent,
    AgentStatus,
    HealthStatus,
    ThreadInfo,
    TurnResult,
    observed_stream,
)
from orchestrator.protocol.events import StreamEvent, EventType
from orchestrator.roles.loader import RoleConfig

logger = logging.getLogger(__name__)


class ClaudeSession:
    """A single Claude Code subprocess session."""

    def __init__(
        self,
        thread_id: str,
        cwd: str | None,
        role: RoleConfig,
        claude_bin: str,
        resume_id: str | None = None,
    ):
        self.thread_id = thread_id
        self.cwd = cwd
        self.role = role
        self._claude_bin = claude_bin
        self._resume_id = resume_id or thread_id
        self._proc: asyncio.subprocess.Process | None = None
        self._turn_active = False

    def _build_cmd(self, message: str) -> list[str]:
        cmd = [
            self._claude_bin,
            "--bare",
            "--output-format", "stream-json",
            "--verbose",
        ]

        if self.role.system_prompt:
            cmd.extend(["--system-prompt", self.role.system_prompt])

        if self.role.model:
            cmd.extend(["--model", self.role.model])

        if self.role.mcp_config:
            cmd.extend(["--mcp-config", self.role.mcp_config])

        for flag in self.role.claude_flags:
            cmd.append(flag)

        # Resume from previous session
        cmd.extend(["--resume", self._resume_id])

        cmd.extend(["--print", message])

        return cmd

    async def send(self, message: str) -> AsyncIterator[StreamEvent]:
        cmd = self._build_cmd(message)
        logger.debug("Claude cmd: %s", " ".join(cmd[:10]) + "...")

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        self._turn_active = True

        try:
            async for line in self._proc.stdout:
                decoded = line.decode().strip()
                if not decoded:
                    continue
                try:
                    data = json.loads(decoded)
                except json.JSONDecodeError:
                    continue

                event = self._parse_claude_event(data)
                if event:
                    yield event
                    if event.is_turn_completed:
                        return

            await self._proc.wait()
            yield StreamEvent(method="turn/completed", params={"threadId": self.thread_id})
        finally:
            self._turn_active = False
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()

    def _parse_claude_event(self, data: dict) -> StreamEvent | None:
        msg_type = data.get("type", "")

        if msg_type == "assistant":
            # Full assistant message block
            content = data.get("message", {}).get("content", [])
            text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            if text_parts:
                return StreamEvent(
                    method="item/agentMessage/delta",
                    params={"threadId": self.thread_id, "textDelta": "".join(text_parts)},
                )

        elif msg_type == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                return StreamEvent(
                    method="item/agentMessage/delta",
                    params={"threadId": self.thread_id, "textDelta": delta.get("text", "")},
                )
            elif delta.get("type") == "thinking_delta":
                return StreamEvent(
                    method="item/reasoning/textDelta",
                    params={"threadId": self.thread_id, "textDelta": delta.get("thinking", "")},
                )

        elif msg_type == "result":
            session_id = data.get("session_id")
            if session_id:
                self._resume_id = session_id
            return StreamEvent(
                method="turn/completed",
                params={
                    "threadId": self.thread_id,
                    "session_id": session_id,
                    "cost_usd": data.get("cost_usd"),
                    "duration_ms": data.get("duration_ms"),
                    "is_error": data.get("is_error", False),
                    "num_turns": data.get("num_turns"),
                },
            )

        elif msg_type == "tool_use":
            tool_name = data.get("name", data.get("tool", ""))
            return StreamEvent(
                method="item/started",
                params={
                    "threadId": self.thread_id,
                    "itemType": "tool_use",
                    "tool": tool_name,
                    "input": data.get("input", {}),
                },
            )

        elif msg_type == "tool_result":
            return StreamEvent(
                method="item/completed",
                params={
                    "threadId": self.thread_id,
                    "itemType": "tool_result",
                    "output": data.get("output", ""),
                },
            )

        return None

    async def interrupt(self):
        if self._proc and self._proc.returncode is None:
            self._proc.send_signal(2)  # SIGINT


class ClaudeAgent(Agent):
    """
    Agent backed by Claude Code CLI subprocesses.

    Each thread is a Claude session that can be resumed.
    Unlike Codex, this is not a persistent server — each turn spawns a new
    subprocess that resumes the conversation via --resume.
    """

    def __init__(
        self,
        agent_id: str,
        role: RoleConfig,
        claude_bin: str | None = None,
    ):
        super().__init__(agent_id, role)
        self._claude_bin = claude_bin or shutil.which("claude") or "claude"
        self._sessions: dict[str, ClaudeSession] = {}

    async def start(self) -> None:
        # Verify claude binary exists
        if not shutil.which(self._claude_bin):
            raise RuntimeError(f"Claude binary not found: {self._claude_bin}")
        self.status = AgentStatus.RUNNING
        logger.info("Claude agent %s ready (bin=%s)", self.name, self._claude_bin)

    async def stop(self) -> None:
        self.status = AgentStatus.STOPPING
        for session in self._sessions.values():
            await session.interrupt()
        self._sessions.clear()
        self.status = AgentStatus.STOPPED
        logger.info("Claude agent %s stopped", self.name)

    async def health_check(self) -> HealthStatus:
        if shutil.which(self._claude_bin):
            return HealthStatus(healthy=True, message="claude binary available")
        return HealthStatus(healthy=False, message="claude binary not found")

    async def start_thread(
        self,
        cwd: str | None = None,
        model: str | None = None,
        thread_id: str | None = None,
    ) -> ThreadInfo:
        tid = thread_id or uuid.uuid4().hex

        effective_role = self.role
        if model:
            # Create a modified role with the requested model
            from dataclasses import replace
            effective_role = replace(self.role, model=model)

        session = ClaudeSession(
            thread_id=tid,
            cwd=cwd,
            role=effective_role,
            claude_bin=self._claude_bin,
        )
        self._sessions[tid] = session

        info = ThreadInfo(
            thread_id=tid,
            agent_id=self.agent_id,
            cwd=cwd,
            model=model or self.role.model,
        )
        self._register_thread(info)
        logger.info("Started Claude thread %s on agent %s", tid[:12], self.name)
        return info

    async def resume_thread(self, thread_id: str) -> ThreadInfo:
        existing = self._get_thread(thread_id)
        if existing:
            return existing
        session = ClaudeSession(
            thread_id=thread_id,
            cwd=None,
            role=self.role,
            claude_bin=self._claude_bin,
            resume_id=thread_id,
        )
        self._sessions[thread_id] = session
        info = ThreadInfo(thread_id=thread_id, agent_id=self.agent_id)
        self._register_thread(info)
        return info

    async def send_message(
        self, thread_id: str, message: str, model: str | None = None
    ) -> TurnResult:
        full_text_parts: list[str] = []
        events: list[StreamEvent] = []
        error: str | None = None

        async for event in self.stream_message(thread_id, message, model):
            events.append(event)
            if event.text_delta:
                full_text_parts.append(event.text_delta)
            if event.event_type == EventType.ERROR:
                error = event.params.get("message", "Unknown error")
            if event.is_turn_completed and event.params.get("is_error"):
                error = "Turn completed with error"

        return TurnResult(
            thread_id=thread_id,
            full_text="".join(full_text_parts),
            events=events,
            error=error,
        )

    @observed_stream
    async def stream_message(
        self, thread_id: str, message: str, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        session = self._sessions.get(thread_id)
        if not session:
            raise ValueError(f"No session for thread {thread_id}")

        async for event in session.send(message):
            yield event

    async def interrupt(self, thread_id: str) -> None:
        session = self._sessions.get(thread_id)
        if session:
            await session.interrupt()

    async def list_threads(self) -> list[ThreadInfo]:
        return list(self._threads.values())
