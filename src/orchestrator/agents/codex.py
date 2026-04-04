"""Codex app-server agent backed by a persistent WebSocket connection."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import signal
import time
from typing import Any, AsyncIterator

import websockets
import websockets.client

from orchestrator.agents.base import (
    Agent,
    AgentStatus,
    HealthStatus,
    ThreadInfo,
    TurnResult,
)
from orchestrator.protocol.events import StreamEvent, EventType
from orchestrator.protocol.jsonrpc import (
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCNotification,
    make_request,
    parse_message,
)
from orchestrator.roles.loader import RoleConfig

logger = logging.getLogger(__name__)


class CodexAgent(Agent):
    """
    Agent backed by a `codex app-server --listen ws://...` process.

    Manages the server process lifecycle and communicates over WebSocket.
    Supports persistent threads, streaming, approvals, and reconnection.
    """

    def __init__(
        self,
        agent_id: str,
        role: RoleConfig,
        port: int,
        host: str = "127.0.0.1",
        codex_bin: str | None = None,
    ):
        super().__init__(agent_id, role)
        self.host = host
        self.port = port
        self._ws_url = f"ws://{host}:{port}"
        self._codex_bin = codex_bin or shutil.which("codex") or "codex"

        self._proc: asyncio.subprocess.Process | None = None
        self._ws: websockets.client.ClientConnection | None = None
        self._reader_task: asyncio.Task | None = None

        self._pending: dict[int | str, asyncio.Future[JSONRPCResponse]] = {}
        self._event_queues: dict[str, asyncio.Queue[StreamEvent]] = {}
        self._global_events: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=10000)
        self._id_counter = 0
        self._initialized = False
        self._reconnect_lock = asyncio.Lock()
        self._shutdown = asyncio.Event()

    @property
    def ws_url(self) -> str:
        return self._ws_url

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        self.status = AgentStatus.STARTING
        await self._start_server_process()
        await self._connect_ws()
        await self._initialize()
        self.status = AgentStatus.RUNNING
        logger.info("Codex agent %s running on %s (pid=%s)", self.name, self._ws_url, self.pid)

    async def _start_server_process(self):
        cmd = [self._codex_bin, "app-server", "--listen", f"ws://{self.host}:{self.port}"]

        for key, value in self.role.config_overrides.items():
            cmd.extend(["-c", f"{key}={json.dumps(value)}" if not isinstance(value, str) else f"{key}={value}"])

        if self.role.model:
            cmd.extend(["-c", f'model="{self.role.model}"'])

        for feat, enabled in self.role.features.items():
            cmd.extend(["--enable" if enabled else "--disable", feat])

        logger.info("Starting codex app-server: %s", " ".join(cmd))

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for the server to be ready
        for _ in range(50):  # 5 seconds max
            await asyncio.sleep(0.1)
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                writer.close()
                await writer.wait_closed()
                return
            except (ConnectionRefusedError, OSError):
                if self._proc.returncode is not None:
                    stderr = await self._proc.stderr.read()
                    raise RuntimeError(
                        f"Codex app-server exited early (code={self._proc.returncode}): "
                        f"{stderr.decode()[:500]}"
                    )

        raise TimeoutError(f"Codex app-server did not start within 5s on port {self.port}")

    async def _connect_ws(self):
        self._ws = await websockets.client.connect(
            self._ws_url,
            max_size=50 * 1024 * 1024,  # 50MB max message
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        )
        self._shutdown.clear()
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"reader-{self.agent_id[:8]}")

    async def _initialize(self):
        result = await self._request("initialize", {
            "clientInfo": {"name": "codex-orchestrator", "version": "0.1.0"},
        })
        await self._notify("initialized")
        self._initialized = True
        logger.info(
            "Agent %s initialized (server=%s)",
            self.name,
            result.get("serverInfo", {}).get("version", "?"),
        )

    async def stop(self) -> None:
        self.status = AgentStatus.STOPPING
        self._shutdown.set()

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
            self._proc = None

        # Resolve any pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Agent stopped"))
        self._pending.clear()

        self.status = AgentStatus.STOPPED
        self._initialized = False
        logger.info("Agent %s stopped", self.name)

    async def health_check(self) -> HealthStatus:
        try:
            import aiohttp
            t0 = time.monotonic()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://{self.host}:{self.port}/healthz", timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    latency = (time.monotonic() - t0) * 1000
                    if resp.status == 200:
                        return HealthStatus(healthy=True, latency_ms=latency)
                    return HealthStatus(healthy=False, message=f"HTTP {resp.status}", latency_ms=latency)
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))

    # ── WebSocket I/O ───────────────────────────────────────────────────

    async def _read_loop(self):
        try:
            async for raw in self._ws:
                if self._shutdown.is_set():
                    break
                try:
                    msg = parse_message(raw)
                except Exception:
                    logger.warning("Unparseable message from %s: %.200s", self.name, raw)
                    continue

                if isinstance(msg, JSONRPCResponse):
                    fut = self._pending.get(msg.id)
                    if fut and not fut.done():
                        fut.set_result(msg)

                elif isinstance(msg, JSONRPCRequest):
                    # Server request (approval) — handle based on policy
                    asyncio.create_task(self._handle_server_request(msg))

                elif isinstance(msg, JSONRPCNotification):
                    event = StreamEvent(
                        method=msg.method,
                        params=msg.params,
                    )
                    # Route to thread-specific queue if possible
                    thread_id = event.thread_id
                    if thread_id and thread_id in self._event_queues:
                        try:
                            self._event_queues[thread_id].put_nowait(event)
                        except asyncio.QueueFull:
                            logger.warning("Event queue full for thread %s", thread_id)
                    # Always put on global queue
                    try:
                        self._global_events.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

        except websockets.exceptions.ConnectionClosed:
            if not self._shutdown.is_set():
                logger.warning("WebSocket connection lost for %s, attempting reconnect", self.name)
                self.status = AgentStatus.DEGRADED
                asyncio.create_task(self._reconnect())
        except asyncio.CancelledError:
            pass

    async def _reconnect(self):
        async with self._reconnect_lock:
            if self._shutdown.is_set() or self.status == AgentStatus.RUNNING:
                return
            for attempt in range(10):
                try:
                    await asyncio.sleep(min(2 ** attempt, 30))
                    await self._connect_ws()
                    await self._initialize()
                    self.status = AgentStatus.RUNNING
                    logger.info("Reconnected agent %s", self.name)
                    return
                except Exception as e:
                    logger.warning("Reconnect attempt %d failed for %s: %s", attempt + 1, self.name, e)
            self.status = AgentStatus.ERROR
            logger.error("Failed to reconnect agent %s after 10 attempts", self.name)

    async def _handle_server_request(self, req: JSONRPCRequest):
        """Handle approval requests from the server."""
        is_approval = req.method in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "execCommandApproval",
            "applyPatchApproval",
            "item/toolRequestUserInput",
        )

        if is_approval:
            decision = self._resolve_approval(req)
            response = {"id": req.id, "result": {"decision": decision}}
            await self._send_raw(response)

            event = StreamEvent(
                method=req.method,
                params={**req.params, "_decision": decision},
                request_id=req.id,
            )
            try:
                self._global_events.put_nowait(event)
            except asyncio.QueueFull:
                pass
        else:
            logger.warning("Unhandled server request: %s", req.method)

    def _resolve_approval(self, req: JSONRPCRequest) -> str:
        """Determine approval decision based on policy."""
        from orchestrator.agents.base import ApprovalPolicy

        if self._approval_policy == ApprovalPolicy.AUTO_APPROVE:
            return "approve"
        if self._approval_policy == ApprovalPolicy.NEVER:
            return "deny"
        # on-failure and on-request: auto-approve for now
        # TODO: route to orchestrator for human-in-the-loop
        return "approve"

    async def _send_raw(self, msg: dict):
        if self._ws:
            await self._ws.send(json.dumps(msg))

    async def _request(self, method: str, params: dict[str, Any], timeout: float = 30) -> dict:
        req_id = self._next_id()
        fut: asyncio.Future[JSONRPCResponse] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        try:
            await self._send_raw({
                "jsonrpc": "2.0", "id": req_id,
                "method": method, "params": params,
            })
            response = await asyncio.wait_for(fut, timeout=timeout)
            if response.error:
                raise RuntimeError(
                    f"RPC error {response.error.code}: {response.error.message}"
                )
            return response.result or {}
        finally:
            self._pending.pop(req_id, None)

    async def _notify(self, method: str, params: dict[str, Any] | None = None):
        await self._send_raw({
            "jsonrpc": "2.0", "method": method, "params": params or {},
        })

    # ── Thread Management ───────────────────────────────────────────────

    async def start_thread(
        self,
        cwd: str | None = None,
        model: str | None = None,
        thread_id: str | None = None,
    ) -> ThreadInfo:
        params: dict[str, Any] = {}
        if cwd:
            params["cwd"] = cwd
        if model or self.role.model:
            params["model"] = model or self.role.model
        params["approvalPolicy"] = self.role.approval_policy

        params["sandbox"] = self.role.sandbox

        # Build strict system prompt using the prompt builder
        from orchestrator.prompts import build_system_prompt
        developer_instructions = build_system_prompt(
            role=self.role.name,
            custom_instructions=self.role.system_prompt,
        )
        params["developerInstructions"] = developer_instructions

        result = await self._request("thread/start", params)

        # Server returns the thread with its ID
        thread_data = result.get("thread", {})
        tid = thread_data.get("id") or thread_data.get("threadId") or result.get("threadId", "")
        effective_model = result.get("model", model or self.role.model)

        self._event_queues[tid] = asyncio.Queue(maxsize=50000)
        info = ThreadInfo(thread_id=tid, agent_id=self.agent_id, cwd=cwd, model=effective_model)
        self._register_thread(info)
        logger.info("Started thread %s on agent %s", tid[:12], self.name)
        return info

    async def resume_thread(self, thread_id: str) -> ThreadInfo:
        self._event_queues.setdefault(thread_id, asyncio.Queue(maxsize=50000))
        await self._request("thread/resume", {"threadId": thread_id})
        info = ThreadInfo(thread_id=thread_id, agent_id=self.agent_id)
        self._register_thread(info)
        return info

    async def fork_thread(self, thread_id: str) -> ThreadInfo:
        import uuid as _uuid
        new_id = _uuid.uuid4().hex
        result = await self._request("thread/fork", {
            "threadId": thread_id, "newThreadId": new_id,
        })
        info = ThreadInfo(thread_id=new_id, agent_id=self.agent_id)
        self._event_queues[new_id] = asyncio.Queue(maxsize=50000)
        self._register_thread(info)
        return info

    async def archive_thread(self, thread_id: str) -> None:
        await self._request("thread/archive", {"threadId": thread_id})
        self._threads.pop(thread_id, None)
        self._event_queues.pop(thread_id, None)

    async def list_threads(self) -> list[ThreadInfo]:
        result = await self._request("thread/list", {})
        threads = []
        for t in result.get("threads", []):
            info = ThreadInfo(
                thread_id=t["threadId"],
                agent_id=self.agent_id,
                status=t.get("status", "active"),
            )
            threads.append(info)
        return threads

    # ── Turn Management ─────────────────────────────────────────────────

    async def send_message(
        self, thread_id: str, message: str, model: str | None = None
    ) -> TurnResult:
        full_text_parts: list[str] = []
        events: list[StreamEvent] = []
        files_changed: list[str] = []
        commands_run: list[str] = []
        error: str | None = None

        async for event in self.stream_message(thread_id, message, model):
            events.append(event)
            if event.text_delta:
                full_text_parts.append(event.text_delta)
            if event.event_type == EventType.FILE_CHANGE_OUTPUT:
                path = event.params.get("filePath")
                if path and path not in files_changed:
                    files_changed.append(path)
            if event.event_type == EventType.COMMAND_EXEC_OUTPUT:
                cmd = event.params.get("command")
                if cmd:
                    commands_run.append(cmd if isinstance(cmd, str) else " ".join(cmd))
            if event.event_type == EventType.ERROR:
                error = event.params.get("message", "Unknown error")

        return TurnResult(
            thread_id=thread_id,
            full_text="".join(full_text_parts),
            events=events,
            files_changed=files_changed,
            commands_run=commands_run,
            error=error,
        )

    async def stream_message(
        self, thread_id: str, message: str, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        queue = self._event_queues.get(thread_id)
        if not queue:
            queue = asyncio.Queue(maxsize=50000)
            self._event_queues[thread_id] = queue

        # Drain any stale events
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": message}],
        }
        if model:
            params["model"] = model

        await self._request("turn/start", params, timeout=10)

        deadline = asyncio.get_event_loop().time() + self.role.max_turn_seconds
        while asyncio.get_event_loop().time() < deadline:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
                yield event
                if event.is_turn_completed:
                    return
            except TimeoutError:
                continue

        logger.warning("Turn timed out on thread %s after %ds", thread_id, self.role.max_turn_seconds)

    async def interrupt(self, thread_id: str) -> None:
        await self._request("turn/interrupt", {"threadId": thread_id})

    # ── Utility ─────────────────────────────────────────────────────────

    async def list_models(self) -> list[dict[str, Any]]:
        result = await self._request("model/list", {})
        return result.get("models", [])

    async def read_config(self, cwd: str | None = None) -> dict:
        return await self._request("config/read", {"cwd": cwd, "includeLayers": False})

    async def get_global_events(self, timeout: float = 0.1) -> list[StreamEvent]:
        events = []
        while True:
            try:
                event = self._global_events.get_nowait()
                events.append(event)
            except asyncio.QueueEmpty:
                break
        return events
