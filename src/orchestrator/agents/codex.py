"""Codex app-server agent backed by a persistent WebSocket connection."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
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
    observed_stream,
)
from orchestrator.protocol.events import StreamEvent, EventType
from orchestrator.protocol.jsonrpc import (
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCNotification,
    make_request,
    parse_message,
)
from orchestrator.processes import process_owns_listener, stop_process_group
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
        self._server_requests: set[asyncio.Task] = set()
        self._reconnect_task: asyncio.Task | None = None

        self._pending: dict[int | str, asyncio.Future[JSONRPCResponse]] = {}
        self._active_turn_ids = {}
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
        try:
            await self._start_server_process()
            await self._connect_ws()
            await self._initialize()
        except BaseException:
            await self.stop()
            raise
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
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # A reachable port alone can belong to another service. Only accept
        # the listener when the OS attributes it to our still-running launcher
        # or a verified descendant in the same managed process group.
        for _ in range(50):
            await asyncio.sleep(0.1)
            if await self._owns_listener():
                return
        raise TimeoutError(f"Codex app-server did not acquire port {self.port}")

    async def _owns_listener(self):
        if not self._proc or self._proc.returncode is not None:
            code = self._proc.returncode if self._proc else 'not started'
            raise RuntimeError(f'Codex app-server exited before listener verification (code={code})')
        owned = await process_owns_listener(self._proc.pid, self.port)
        if self._proc.returncode is not None:
            raise RuntimeError('Codex app-server exited during listener verification')
        return owned

    async def _connect_ws(self):
        if not await self._owns_listener():
            raise RuntimeError('Codex app-server does not own its listener')
        ws = await websockets.client.connect(
            self._ws_url,
            max_size=50 * 1024 * 1024,  # 50MB max message
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        )
        try:
            if not await self._owns_listener():
                raise RuntimeError('Codex listener ownership changed during connection')
        except BaseException:
            await ws.close()
            raise
        self._ws = ws
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
        if self._reconnect_task:
            self._reconnect_task.cancel()
            await asyncio.gather(self._reconnect_task, return_exceptions=True)
            self._reconnect_task = None
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        # The reader is stopped first, so it cannot create more input handlers.
        requests = list(self._server_requests)
        for task in requests:
            task.cancel()
        await asyncio.gather(*requests, return_exceptions=True)

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._proc:
            await stop_process_group(self._proc)
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
                    task = asyncio.create_task(self._handle_server_request(msg))
                    self._server_requests.add(task)
                    task.add_done_callback(self._server_requests.discard)

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
                self._reconnect_task = asyncio.create_task(self._reconnect())
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

        if req.method == "item/toolRequestUserInput" and getattr(self, 'input_handler', None):
            try:
                answer = await self.input_handler(getattr(self, 'task_id', None),
                    json.dumps(req.params.get('questions', [])), kind='backend_input', agent_id=self.agent_id,
                    options=req.params.get('questions', []))
                if not isinstance(answer, dict):
                    answer = {'answers': {q['id']: {'answers': [str(answer)]} for q in req.params.get('questions', [])}}
                # toolRequestUserInput accepts answers only. Queue the current
                # resource context through native turn/steer before unblocking it.
                context = await self.prepare_input('Files supplied with the human response are available below.')
                if context.attachments:
                    thread_id = req.params.get('threadId')
                    turn_id = req.params.get('turnId') or self._active_turn_ids.get(thread_id)
                    if not thread_id or not turn_id:
                        raise ValueError('Backend did not identify the waiting turn; retry with a current Codex app-server')
                    await self._request('turn/steer', {'threadId': thread_id, 'expectedTurnId': turn_id,
                                                     'input': self.attachment_input(context)})
                await self._send_raw({'id': req.id, 'result': answer})
            except asyncio.CancelledError:
                raise
            except (ValueError, RuntimeError) as exc:
                await self._send_raw({'id': req.id, 'error': {'code': -32000, 'message': str(exc)}})
                queue = self._event_queues.get(req.params.get('threadId'))
                if queue is not None:
                    await queue.put(StreamEvent(method='error', params={'message': 'Attachment delivery failed: ' + str(exc)}))
            except TimeoutError:
                await self._send_raw({'id': req.id, 'error': {'code': -32000, 'message': 'Input expired'}})
            return

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
        params["approvalPolicy"] = (
            "on-request" if self.role.approval_policy == "on-failure" else self.role.approval_policy
        )

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
        saved = await self.store.get_thread(thread_id) if getattr(self, 'store', None) else None
        await self._request("thread/resume", {"threadId": thread_id})
        self._event_queues[thread_id] = asyncio.Queue(maxsize=50000)
        info = ThreadInfo(thread_id=thread_id, agent_id=self.agent_id, cwd=saved.cwd if saved else None)
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
        token_usage: dict[str, int] = {}
        error: str | None = None
        completed = False
        final_text: str | None = None
        t0 = time.monotonic()

        async for event in self.stream_message(thread_id, message, model):
            events.append(event)
            if event.text_delta:
                full_text_parts.append(event.text_delta)
            item = event.params.get("item", {})
            if (event.method == "item/completed" and item.get("type") == "agentMessage"
                    and item.get("phase") == "final_answer"):
                final_text = item.get("text", "")
            if event.event_type == EventType.FILE_CHANGE_OUTPUT:
                path = event.params.get("filePath")
                if path and path not in files_changed:
                    files_changed.append(path)
            if event.event_type == EventType.COMMAND_EXEC_OUTPUT:
                cmd = event.params.get("command")
                if cmd:
                    commands_run.append(cmd if isinstance(cmd, str) else " ".join(cmd))
            if event.event_type == EventType.ERROR:
                detail = event.params.get("error", event.params)
                error = detail.get("message", "Unknown error") if isinstance(detail, dict) else str(detail)
            # Capture token usage from thread/tokenUsage/updated events
            if event.method in ("thread/tokenUsage/updated", EventType.THREAD_TOKEN_USAGE):
                usage = event.params.get("tokenUsage", {})
                total = usage.get("total", {})
                if total:
                    token_usage = {
                        "input_tokens": total.get("inputTokens", 0),
                        "output_tokens": total.get("outputTokens", 0) + total.get("outputTokensDetails", {}).get("reasoningTokens", 0),
                        "cached_tokens": total.get("cachedInputTokens", 0),
                        "total_tokens": total.get("totalTokens", 0),
                    }
            # Also capture from turn/completed
            if event.is_turn_completed:
                completed = True
                turn_data = event.params.get("turn", {})
                if turn_data.get("status") in {"failed", "interrupted"}:
                    detail = turn_data.get("error") or {}
                    error = detail.get("message") or f"Turn {turn_data['status']}"
                if "tokenUsage" in turn_data:
                    tu = turn_data["tokenUsage"].get("total", {})
                    if tu:
                        token_usage = {
                            "input_tokens": tu.get("inputTokens", 0),
                            "output_tokens": tu.get("outputTokens", 0),
                            "cached_tokens": tu.get("cachedInputTokens", 0),
                            "total_tokens": tu.get("totalTokens", 0),
                        }

        duration_ms = int((time.monotonic() - t0) * 1000)
        if not completed and not error:
            error = "Turn ended without completion; implementation is not ready for review"

        return TurnResult(
            thread_id=thread_id,
            full_text=final_text if final_text is not None else "".join(full_text_parts),
            events=events,
            token_usage={**token_usage, "duration_ms": duration_ms},
            files_changed=files_changed,
            commands_run=commands_run,
            error=error,
        )

    @staticmethod
    def attachment_input(message):
        text = message.with_manifest() if hasattr(message, 'with_manifest') else str(message)
        return [{'type': 'text', 'text': text}] + [
            {'type': 'localImage', 'path': a['path']} for a in getattr(message, 'attachments', []) if a['width']]

    async def validate_attachment_input(self, message):
        if not any(a['width'] for a in message.attachments):
            return
        models = await self.list_models()
        selected = next((m for m in models if m.get('model') == self.role.model or m.get('id') == self.role.model), None)
        if selected is None and not self.role.model:
            selected = next((m for m in models if m.get('isDefault')), None)
        if not selected or 'image' not in selected.get('inputModalities', []):
            raise ValueError(f'Codex model {self.role.model or "default"} does not advertise image input. Select an image-capable model or remove the images.')

    @observed_stream
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
            "input": self.attachment_input(message),
        }
        if model:
            params["model"] = model

        started = await self._request("turn/start", params, timeout=10)
        self._active_turn_ids[thread_id] = started.get("turn", {}).get("id")

        deadline = (asyncio.get_event_loop().time() + self.role.max_turn_seconds
                    if self.role.max_turn_seconds > 0 else None)
        while deadline is None or asyncio.get_event_loop().time() < deadline:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
                yield event
                if event.is_turn_completed:
                    return
            except TimeoutError:
                continue

        logger.warning("Turn timed out on thread %s after %ds", thread_id, self.role.max_turn_seconds)
        try:
            # Interrupt acknowledgement does not mean the turn's tools have
            # stopped. Recovery must not inspect a checkout still being edited.
            async with asyncio.timeout(30):
                await self.interrupt(thread_id)
                while True:
                    event = await queue.get()
                    yield event
                    if event.is_turn_completed:
                        break
        except asyncio.CancelledError:
            await self.stop()
            raise
        except Exception as exc:
            logger.warning("Timed-out turn %s did not confirm shutdown; stopping agent: %s", thread_id, exc)
            await self.stop()
        yield StreamEvent(method="error", params={
            "message": f"Turn timed out after {self.role.max_turn_seconds}s; resume implementation before review",
        })

    async def interrupt(self, thread_id: str) -> None:
        params = {"threadId": thread_id}
        turn_id = self._active_turn_ids.get(thread_id)
        if turn_id:
            params["turnId"] = turn_id
        await self._request("turn/interrupt", params)

    # ── Utility ─────────────────────────────────────────────────────────

    async def list_models(self) -> list[dict[str, Any]]:
        models = []
        params: dict[str, Any] = {"includeHidden": True}
        seen_cursors = set()
        while True:
            result = await self._request("model/list", params)
            page = result.get("data")
            if not isinstance(page, list):
                raise ValueError("Codex model discovery returned no model data. Update the Codex CLI and retry.")
            models.extend(page)
            cursor = result.get("nextCursor")
            if cursor is None:
                return models
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise ValueError("Codex model discovery returned an invalid pagination cursor. Restart Codex and retry.")
            seen_cursors.add(cursor)
            params = {"includeHidden": True, "cursor": cursor}

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
