"""Claude Code agent backed by a persistent subprocess."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
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
from orchestrator.processes import stop_process_group
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
        self._resume_id = resume_id
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
        if self._resume_id:
            cmd.extend(["--resume", self._resume_id])
        else:
            cmd.extend(["--session-id", self.thread_id])

        if getattr(message, 'attachments', []):
            cmd.extend(['--input-format', 'stream-json', '--print'])
            # Read permissions for exact resources only. No additional workspace
            # roots or write permissions are granted for attachments.
            cmd.extend(['--settings', json.dumps({'permissions': {'allow': [
                'Read(/' + ''.join(('\\' + char) if char in '\\*?[]!' else char for char in a['path']) + ')' for a in message.attachments]}})])
        else:
            cmd.extend(["--print", str(message)])

        return cmd

    async def send(self, message: str) -> AsyncIterator[StreamEvent]:
        cmd = self._build_cmd(message)
        logger.debug("Claude cmd: %s", " ".join(cmd[:10]) + "...")

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            start_new_session=True,
            stdin=asyncio.subprocess.PIPE if getattr(message, "attachments", []) else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        self._turn_active = True
        async def write_input():
            if not getattr(message, 'attachments', []):
                return
            blocks = [{'type': 'text', 'text': message.with_manifest()}]
            for attachment in message.attachments:
                if attachment['width']:
                    blocks.append({'type': 'image', 'source': {'type': 'base64',
                        'media_type': attachment['media_type'],
                        'data': base64.b64encode(Path(attachment['path']).read_bytes()).decode('ascii')}})
            payload = {'type': 'user', 'message': {'role': 'user', 'content': blocks},
                       'parent_tool_use_id': None, 'session_id': self._resume_id or self.thread_id}
            self._proc.stdin.write((json.dumps(payload) + '\n').encode())
            await self._proc.stdin.drain()
            self._proc.stdin.close()
        writer = asyncio.create_task(write_input())
        async def drain_errors():
            tail = b''
            while chunk := await self._proc.stderr.read(8192):
                tail = (tail + chunk)[-8192:]
            return tail
        stderr = asyncio.create_task(drain_errors())
        try:
            async for line in self._proc.stdout:
                decoded = line.decode().strip()
                if not decoded:
                    continue
                try:
                    data = json.loads(decoded)
                except json.JSONDecodeError:
                    continue

                # Claude embeds tool calls/results in message content blocks.
                if data.get('type') in {'assistant', 'user'}:
                    for block in data.get('message', {}).get('content', []):
                        if block.get('type') in {'tool_use', 'tool_result'}:
                            yield StreamEvent(method='item/completed', params={'threadId': self.thread_id, 'item': block})
                event = self._parse_claude_event(data)
                if event:
                    yield event
                    if event.is_turn_completed:
                        return

            await writer
            await self._proc.wait()
            if self._proc.returncode:
                # Do not persist stderr: backend errors may echo native image input.
                yield StreamEvent(method='error', params={'message': f'Claude exited with status {self._proc.returncode}. Verify CLI stream-json support and model image capability.'})
            else:
                yield StreamEvent(method="error", params={
                    "threadId": self.thread_id,
                    "message": "Claude exited without a completion result; implementation is not ready for review",
                })
        finally:
            writer.cancel()
            stderr.cancel()
            await asyncio.gather(writer, stderr, return_exceptions=True)
            self._turn_active = False
            if self._proc:
                await stop_process_group(self._proc)

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
        if self._proc:
            await stop_process_group(self._proc)


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
        tid = thread_id or str(uuid.uuid4())

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
        if existing and thread_id in self._sessions:
            return existing
        saved = await self.store.get_thread(thread_id) if getattr(self, 'store', None) else None
        session = ClaudeSession(
            thread_id=thread_id,
            cwd=saved.cwd if saved else None,
            role=self.role,
            claude_bin=self._claude_bin,
            resume_id=saved.metadata.get("session_id", thread_id) if saved else thread_id,
        )
        self._sessions[thread_id] = session
        info = ThreadInfo(thread_id=thread_id, agent_id=self.agent_id, cwd=saved.cwd if saved else None)
        self._register_thread(info)
        return info

    async def send_message(
        self, thread_id: str, message: str, model: str | None = None
    ) -> TurnResult:
        full_text_parts: list[str] = []
        events: list[StreamEvent] = []
        error: str | None = None
        completed = False

        async for event in self.stream_message(thread_id, message, model):
            events.append(event)
            if event.text_delta:
                full_text_parts.append(event.text_delta)
            if event.event_type == EventType.ERROR:
                error = event.params.get("message", "Unknown error")
            if event.is_turn_completed and event.params.get("is_error"):
                error = "Turn completed with error"
            completed = completed or event.is_turn_completed

        if not completed and not error:
            error = "Turn ended without completion; implementation is not ready for review"

        return TurnResult(
            thread_id=thread_id,
            full_text="".join(full_text_parts),
            events=events,
            error=error,
        )

    async def validate_attachment_input(self, message):
        if not message.attachments:
            return
        if not getattr(self, '_attachment_transport_checked', False):
            proc = await asyncio.create_subprocess_exec(self._claude_bin, '--help',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), 10)
            except BaseException:
                proc.kill()
                await proc.wait()
                raise
            if proc.returncode or b'--input-format' not in stdout or b'stream-json' not in stdout:
                raise ValueError('Installed Claude CLI lacks stream-json image input. Upgrade Claude Code before using attachments.')
            self._attachment_transport_checked = True
        # Built-in Claude model families support images. Custom providers must
        # advertise a known model rather than silently discarding native blocks.
        if any(a['width'] and a['byte_count'] > 5 * 1024 * 1024 for a in message.attachments):
            raise ValueError('Claude native images must be at most 5 MiB each. Choose a smaller image before starting work.')
        model = self.role.model or ''
        if any(a['width'] for a in message.attachments) and model and not any(name in model.lower() for name in ('claude', 'sonnet', 'opus', 'haiku')):
            raise ValueError('This Claude model alias has no verified image capability. Select a Claude image-capable model or remove images.')

    @observed_stream
    async def stream_message(
        self, thread_id: str, message: str, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        session = self._sessions.get(thread_id)
        if not session:
            raise ValueError(f"No session for thread {thread_id}")

        try:
            async with asyncio.timeout(self.role.max_turn_seconds):
                async for event in session.send(message):
                    yield event
        except TimeoutError:
            await session.interrupt()
            yield StreamEvent(method="error", params={
                "threadId": thread_id,
                "message": f"Turn timed out after {self.role.max_turn_seconds}s; resume implementation before review",
            })

    async def interrupt(self, thread_id: str) -> None:
        session = self._sessions.get(thread_id)
        if session:
            await session.interrupt()

    async def list_threads(self) -> list[ThreadInfo]:
        return list(self._threads.values())
