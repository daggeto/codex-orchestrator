"""Agent registry — lifecycle management, health monitoring, port allocation."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from orchestrator.agents.base import Agent, AgentStatus, HealthStatus
from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.claude import ClaudeAgent
from orchestrator.config import OrchestratorConfig
from orchestrator.events.bus import EventBus
from orchestrator.roles.loader import RoleConfig
from orchestrator.state.store import AgentRecord, StateStore

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Manages agent lifecycle, health monitoring, and discovery."""

    def __init__(self, config: OrchestratorConfig, store: StateStore, bus: EventBus):
        self._config = config
        self._store = store
        self._bus = bus
        self._agents: dict[str, Agent] = {}
        self._allocated_ports: set[int] = set()
        self._health_task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()
        self._observations: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._observation_task: asyncio.Task | None = None

    def _observe(self, topic: str, payload: dict[str, Any]):
        """Drop excess observations; never put event-bus latency on a turn."""
        try:
            self._observations.put_nowait((topic, payload))
        except asyncio.QueueFull:
            return
        if self._observation_task is None or self._observation_task.done():
            self._observation_task = asyncio.create_task(self._forward_observations())

    async def _forward_observations(self):
        while True:
            topic, payload = await self._observations.get()
            try:
                await asyncio.wait_for(
                    self._bus.publish(topic, payload, source="registry"), timeout=0.25,
                )
            except Exception:
                logger.debug("Observation forwarding failed", exc_info=True)
            finally:
                self._observations.task_done()

    @property
    def agents(self) -> dict[str, Agent]:
        return dict(self._agents)

    def _allocate_port(self) -> int:
        port = self._config.base_port
        while port in self._allocated_ports:
            port += 1
            if port > self._config.base_port + 1000:
                raise RuntimeError("No available ports in range")
        self._allocated_ports.add(port)
        return port

    def _release_port(self, port: int):
        self._allocated_ports.discard(port)

    async def create_agent(
        self,
        role: RoleConfig,
        agent_id: str | None = None,
    ) -> Agent:
        aid = agent_id or uuid.uuid4().hex[:16]

        if len(self._agents) >= self._config.max_agents:
            raise RuntimeError(f"Maximum agents ({self._config.max_agents}) reached")

        if role.backend == "codex":
            port = self._allocate_port()
            agent = CodexAgent(agent_id=aid, role=role, port=port)
        elif role.backend == "claude":
            agent = ClaudeAgent(agent_id=aid, role=role)
        else:
            raise ValueError(f"Unknown backend: {role.backend}")

        agent.set_observer(self._observations)
        if self._observation_task is None or self._observation_task.done():
            self._observation_task = asyncio.create_task(self._forward_observations())
        self._agents[aid] = agent

        record = AgentRecord(
            agent_id=aid,
            role=role.name,
            backend=role.backend,
            port=getattr(agent, "port", None),
            status="stopped",
            config={
                "model": role.model,
                "sandbox": role.sandbox,
                "approval_policy": role.approval_policy,
            },
        )
        await self._store.save_agent(record)

        await self._bus.publish("agent.created", {
            "agent_id": aid, "role": role.name, "backend": role.backend,
        }, source="registry")

        logger.info("Created agent %s (role=%s, backend=%s)", aid[:12], role.name, role.backend)
        return agent

    async def start_agent(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if not agent:
            raise KeyError(f"Agent {agent_id} not found")
        if agent.status == AgentStatus.RUNNING:
            return

        try:
            await agent.start()
            await self._store.update_agent_status(
                agent_id, "running",
                pid=getattr(agent, "pid", None),
                ws_url=getattr(agent, "ws_url", None),
            )
            await self._bus.publish("agent.started", {
                "agent_id": agent_id, "role": agent.role.name,
            }, source="registry")
        except Exception as e:
            agent.status = AgentStatus.ERROR
            await self._store.update_agent_status(agent_id, "error")
            await self._bus.publish("agent.error", {
                "agent_id": agent_id, "error": str(e),
            }, source="registry")
            raise

    async def stop_agent(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if not agent:
            raise KeyError(f"Agent {agent_id} not found")

        try:
            await agent.stop()
        finally:
            if isinstance(agent, CodexAgent):
                self._release_port(agent.port)
            await self._store.update_agent_status(agent_id, "stopped")
            await self._bus.publish("agent.stopped", {
                "agent_id": agent_id, "role": agent.role.name,
            }, source="registry")

    async def destroy_agent(self, agent_id: str) -> None:
        if agent_id in self._agents:
            agent = self._agents[agent_id]
            if agent.status == AgentStatus.RUNNING:
                await self.stop_agent(agent_id)
            del self._agents[agent_id]
        await self._store.delete_agent(agent_id)
        await self._bus.publish("agent.destroyed", {"agent_id": agent_id}, source="registry")

    def get_agent(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def get_agents_by_role(self, role_name: str) -> list[Agent]:
        return [a for a in self._agents.values() if a.role.name == role_name]

    def get_running_agents(self) -> list[Agent]:
        return [a for a in self._agents.values() if a.status == AgentStatus.RUNNING]

    # ── Health Monitoring ───────────────────────────────────────────────

    async def start_health_monitor(self):
        self._shutdown.clear()
        self._health_task = asyncio.create_task(self._health_loop())

    async def stop_health_monitor(self):
        self._shutdown.set()
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

    async def _health_loop(self):
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(self._config.health_check_interval)
                await self._check_all_health()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Health check loop error")

    async def _check_all_health(self):
        running = self.get_running_agents()
        if not running:
            return

        results = await asyncio.gather(
            *[a.health_check() for a in running],
            return_exceptions=True,
        )

        for agent, result in zip(running, results):
            if isinstance(result, Exception):
                health = HealthStatus(healthy=False, message=str(result))
            else:
                health = result

            if not health.healthy and agent.status == AgentStatus.RUNNING:
                agent.status = AgentStatus.DEGRADED
                await self._store.update_agent_status(agent.agent_id, "degraded")
                await self._bus.publish("agent.degraded", {
                    "agent_id": agent.agent_id,
                    "message": health.message,
                }, source="registry")
                logger.warning("Agent %s degraded: %s", agent.name, health.message)
            elif health.healthy and agent.status == AgentStatus.DEGRADED:
                agent.status = AgentStatus.RUNNING
                await self._store.update_agent_status(agent.agent_id, "running")
                logger.info("Agent %s recovered", agent.name)

    # ── Restore from persisted state ────────────────────────────────────

    async def restore(self, roles: dict[str, RoleConfig]) -> int:
        """Restore agents from database on startup. Returns count of restored agents."""
        records = await self._store.list_agents()
        restored = 0
        for rec in records:
            if rec.role not in roles:
                logger.warning("Skipping agent %s: role '%s' not found", rec.agent_id, rec.role)
                continue
            role = roles[rec.role]
            try:
                agent = await self.create_agent(role, agent_id=rec.agent_id)
                if rec.status in ("running", "degraded"):
                    await self.start_agent(rec.agent_id)
                    restored += 1
            except Exception:
                logger.exception("Failed to restore agent %s", rec.agent_id)
        return restored

    # ── Cleanup ─────────────────────────────────────────────────────────

    async def shutdown(self):
        """Stop all agents and clean up."""
        await self.stop_health_monitor()
        for agent_id in list(self._agents):
            try:
                await self.stop_agent(agent_id)
            except Exception:
                logger.exception("Error stopping agent %s", agent_id)
        self._agents.clear()
        self._allocated_ports.clear()
        if self._observation_task:
            self._observation_task.cancel()
            await asyncio.gather(self._observation_task, return_exceptions=True)
        while not self._observations.empty():
            self._observations.get_nowait()
