"""Main Orchestrator — top-level coordinator that ties everything together."""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any

from orchestrator.agents.base import AgentStatus
from orchestrator.agents.registry import AgentRegistry
from orchestrator.config import OrchestratorConfig
from orchestrator.events.bus import EventBus
from orchestrator.hitl import HITLManager
from orchestrator.roles.loader import RoleConfig, load_all_roles
from orchestrator.skills import SkillRegistry, create_default_registry
from orchestrator.state.store import StateStore, WorkItemRecord, WorkItemStatus
from orchestrator.supervision import Supervisor, SupervisorConfig
from orchestrator.workflows.builtins import BUILTIN_WORKFLOWS
from orchestrator.workflows.engine import Workflow, WorkflowEngine, WorkflowResult
from orchestrator.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Top-level coordinator.

    Manages the full lifecycle: loading roles, starting agents,
    running workflows, handling work items, and exposing an event bus
    for external consumers (CLI, API).

    Includes:
    - Audit: full turn-level logging with tokens, cost, timing, model, interactions
    - Supervision: ensures agents produce deliverables, not noise
    - HITL: approval gates, escalation, and human intervention
    - Skills: composable, reusable agent capabilities
    - Handoff: context transfer between agents
    """

    def __init__(self, config: OrchestratorConfig | None = None):
        self.config = config or OrchestratorConfig()
        self.bus = EventBus()
        self.store = StateStore(self.config.db_path)
        self.registry = AgentRegistry(self.config, self.store, self.bus)
        self.engine = WorkflowEngine(self.registry, self.store, self.bus)
        self.supervisor = Supervisor(bus=self.bus)
        self.hitl = HITLManager(self.bus)
        self.skills = create_default_registry()
        self.workspace = WorkspaceManager(self.config.data_dir / "worktrees")
        self.audit: "AuditLog | None" = None
        self.roles: dict[str, RoleConfig] = {}
        self.workflows: dict[str, Workflow] = dict(BUILTIN_WORKFLOWS)
        self._running = False
        self._shutdown_event = asyncio.Event()

    async def start(self, restore: bool = True) -> None:
        """Initialize store, audit log, load roles, skills, optionally restore agents."""
        await self.store.init()

        # Init audit log on the same DB connection
        from orchestrator.audit import AuditLog
        self.audit = AuditLog(self.store._db)
        await self.audit.init()
        self._load_roles()
        self._load_skills()
        self._load_custom_workflows()

        if restore:
            n = await self.registry.restore(self.roles)
            if n:
                logger.info("Restored %d agent(s)", n)

        await self.registry.start_health_monitor()
        self._running = True
        logger.info(
            "Orchestrator started (roles=%d, workflows=%d, agents=%d)",
            len(self.roles), len(self.workflows), len(self.registry.agents),
        )

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        self._shutdown_event.set()
        await self.registry.shutdown()
        # Don't auto-cleanup worktrees — they have uncommitted work
        # User should explicitly merge or abandon via CLI/API
        await self.store.close()
        logger.info("Orchestrator stopped")

    def _load_roles(self):
        # Load from configured roles dir
        roles_dir = self.config.effective_roles_dir
        if roles_dir.exists():
            self.roles.update(load_all_roles(roles_dir))
            logger.info("Loaded %d role(s) from %s", len(self.roles), roles_dir)
        # Also check project-local roles/
        local_roles = Path.cwd() / "roles"
        if local_roles.exists() and local_roles != roles_dir:
            self.roles.update(load_all_roles(local_roles))

    def _load_skills(self):
        skills_dir = self.config.data_dir / "skills"
        if skills_dir.exists():
            self.skills.load_dir(skills_dir)
        local_skills = Path.cwd() / "skills"
        if local_skills.exists() and local_skills != skills_dir:
            self.skills.load_dir(local_skills)
        logger.info("Loaded %d skill(s)", len(self.skills.list_all()))

    def _load_custom_workflows(self):
        # Placeholder for loading workflows from TOML configs
        wf_dir = self.config.effective_workflows_dir
        if wf_dir.exists():
            logger.info("Custom workflow loading from %s (not yet implemented)", wf_dir)

    # ── Agent Management ────────────────────────────────────────────────

    async def spawn_agent(self, role_name: str, agent_id: str | None = None) -> str:
        """Create and start an agent with the given role. Returns agent_id."""
        role = self.roles.get(role_name)
        if not role:
            raise ValueError(f"Unknown role: {role_name}. Available: {list(self.roles.keys())}")
        agent = await self.registry.create_agent(role, agent_id=agent_id)
        await self.registry.start_agent(agent.agent_id)
        return agent.agent_id

    async def spawn_team(self, role_names: list[str] | None = None) -> dict[str, str]:
        """Ensure each requested role has a started agent, reusing saved agents."""
        if role_names is None:
            role_names = list(self.roles.keys())
        role_names = list(dict.fromkeys(role_names))
        existing = {}
        for name in role_names:
            if name not in self.roles:
                raise ValueError(f"Unknown role: {name}. Available: {list(self.roles.keys())}")
            agents = self.registry.get_agents_by_role(name)
            if agents:
                existing[name] = next(
                    (a for a in agents if a.status in (AgentStatus.RUNNING, AgentStatus.DEGRADED)),
                    agents[0],
                )

        needed = len(role_names) - len(existing)
        available = self.config.max_agents - len(self.registry.agents)
        if needed > available:
            raise RuntimeError(
                f"Team needs {needed} new agent(s), but only {available} slot(s) "
                f"remain (max_agents={self.config.max_agents}). "
                "Remove unused agents or increase max_agents in the orchestrator config."
            )

        result = {}
        for name in role_names:
            if name in existing:
                agent = existing[name]
                if agent.status not in (AgentStatus.RUNNING, AgentStatus.DEGRADED):
                    await self.registry.start_agent(agent.agent_id)
                aid = agent.agent_id
            else:
                aid = await self.spawn_agent(name)
            result[name] = aid
        return result

    # ── Work Items ──────────────────────────────────────────────────────

    async def create_work_item(
        self,
        title: str,
        description: str = "",
        priority: int = 0,
        tags: list[str] | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> str:
        """Create a new work item. Returns item_id."""
        import uuid
        item = WorkItemRecord(
            item_id=uuid.uuid4().hex[:16],
            title=title,
            description=description,
            priority=priority,
            tags=tags or [],
            input_data=input_data or {},
        )
        await self.store.save_work_item(item)
        await self.bus.publish("work.created", {
            "item_id": item.item_id, "title": title,
        }, source="orchestrator")
        return item.item_id

    async def assign_work(
        self,
        item_id: str,
        role: str,
        message: str | None = None,
        isolate: bool | None = None,
    ) -> str:
        """
        Assign a work item to an agent by role, send message, return response.

        If isolate=True (or auto for write-capable roles), creates a git worktree
        so the agent works on an isolated branch. Read-only roles share the main repo.
        """
        agents = self.registry.get_agents_by_role(role)
        running = [a for a in agents if a.status.value == "running"]
        if not running:
            raise RuntimeError(f"No running agents with role '{role}'")
        agent = running[0]

        item = await self.store.get_work_item(item_id)
        if not item:
            raise ValueError(f"Work item {item_id} not found")

        repo_path = item.input_data.get("cwd")

        # Determine if we need workspace isolation
        should_isolate = isolate
        if should_isolate is None:
            # Auto: isolate write-capable roles
            should_isolate = agent.role.sandbox in ("workspace-write", "danger-full-access")

        work_cwd = repo_path
        worktree = None

        if should_isolate and repo_path:
            try:
                worktree = await self.workspace.create_worktree(
                    repo_path=repo_path,
                    agent_id=agent.agent_id,
                    work_item_id=item_id,
                )
                work_cwd = worktree.worktree_path
                logger.info(
                    "Isolated work item %s in worktree %s (branch=%s)",
                    item_id, worktree.worktree_id, worktree.branch,
                )
            except Exception as e:
                logger.warning("Failed to create worktree, using main repo: %s", e)

        await self.store.update_work_item_status(
            item_id, WorkItemStatus.IN_PROGRESS,
            history_entry={
                "action": "assigned",
                "agent": agent.agent_id,
                "role": role,
                "worktree_id": worktree.worktree_id if worktree else None,
                "worktree_branch": worktree.branch if worktree else None,
                "cwd": work_cwd,
            },
        )

        msg = message or item.description or item.title
        thread_info = await agent.start_thread(cwd=work_cwd)
        result = await agent.send_message(thread_info.thread_id, msg)

        await self.store.update_work_item_status(
            item_id,
            WorkItemStatus.COMPLETED if result.ok else WorkItemStatus.FAILED,
            output_data={"response": result.full_text, "files": result.files_changed},
            history_entry={"action": "completed", "agent": agent.agent_id, "success": result.ok},
        )

        return result.full_text

    # ── Workflow Execution ──────────────────────────────────────────────

    async def run_workflow(
        self,
        workflow_name: str,
        variables: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Run a named workflow."""
        wf = self.workflows.get(workflow_name)
        if not wf:
            raise ValueError(f"Unknown workflow: {workflow_name}. Available: {list(self.workflows.keys())}")
        return await self.engine.run(wf, variables=variables)

    # ── Direct Agent Interaction ────────────────────────────────────────

    async def send_to_role(
        self,
        role: str,
        message: str,
        cwd: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """Send a message to an agent by role. Returns the response text."""
        agents = self.registry.get_agents_by_role(role)
        running = [a for a in agents if a.status.value == "running"]
        if not running:
            raise RuntimeError(f"No running agents with role '{role}'")
        agent = running[0]

        if thread_id:
            pass
        else:
            info = await agent.start_thread(cwd=cwd)
            thread_id = info.thread_id
            await self._persist_thread(info)

        result = await agent.send_message(thread_id, message)
        await self._audit_turn(agent, thread_id, message, result, cwd=cwd)
        return result.full_text

    async def send_to_agent(
        self,
        agent_id: str,
        message: str,
        thread_id: str | None = None,
        cwd: str | None = None,
    ) -> str:
        """Send a message to a specific agent. Returns the response text."""
        agent = self.registry.get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        if not thread_id:
            info = await agent.start_thread(cwd=cwd)
            thread_id = info.thread_id
            await self._persist_thread(info)

        result = await agent.send_message(thread_id, message)
        await self._audit_turn(agent, thread_id, message, result, cwd=cwd)
        return result.full_text

    async def _persist_thread(self, info: "ThreadInfo"):
        from orchestrator.state.store import ThreadRecord
        rec = ThreadRecord(
            thread_id=info.thread_id,
            agent_id=info.agent_id,
            cwd=info.cwd,
            model=info.model,
            status=info.status,
        )
        await self.store.save_thread(rec)

    async def _audit_turn(
        self,
        agent: "Agent",
        thread_id: str,
        message: str,
        result: "TurnResult",
        cwd: str | None = None,
        supervision_result: Any | None = None,
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
    ):
        """Record a turn in the audit log."""
        if not self.audit:
            return
        from orchestrator.audit import TurnRecord

        tu = result.token_usage
        rec = TurnRecord(
            agent_id=agent.agent_id,
            role=agent.role.name,
            thread_id=thread_id,
            message=message,
            response=result.full_text,
            duration_ms=tu.get("duration_ms"),
            input_tokens=tu.get("input_tokens"),
            output_tokens=tu.get("output_tokens"),
            cached_tokens=tu.get("cached_tokens"),
            total_tokens=tu.get("total_tokens"),
            model=agent.role.model,
            files_changed=result.files_changed,
            commands_run=result.commands_run,
            error=result.error,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            cwd=cwd,
        )

        if supervision_result:
            rec.supervision_accepted = supervision_result.accepted
            rec.supervision_noise_ratio = supervision_result.noise_ratio
            rec.supervision_issues = supervision_result.issues
            rec.deliverables = [
                {"type": d.dtype.value, "evidence": d.evidence, "verified": d.verified}
                for d in supervision_result.deliverables
            ]

        await self.audit.log_turn(rec)

    # ── Supervised Interaction ──────────────────────────────────────────

    async def supervised_send(
        self,
        role: str,
        message: str,
        cwd: str | None = None,
        thread_id: str | None = None,
    ) -> "SupervisionResult":
        """Send a message with supervision — ensures deliverables, not noise."""
        from orchestrator.supervision import SupervisionResult

        agents = self.registry.get_agents_by_role(role)
        running = [a for a in agents if a.status.value == "running"]
        if not running:
            raise RuntimeError(f"No running agents with role '{role}'")
        agent = running[0]

        if not thread_id:
            info = await agent.start_thread(cwd=cwd)
            thread_id = info.thread_id
            await self._persist_thread(info)

        return await self.supervisor.supervised_send(agent, thread_id, message)

    # ── Skill Execution ─────────────────────────────────────────────────

    async def run_skill(
        self,
        skill_name: str,
        role: str,
        variables: dict[str, Any] | None = None,
        cwd: str | None = None,
        supervised: bool = True,
    ) -> str:
        """Run a skill on an agent with optional supervision."""
        skill = self.skills.get(skill_name)
        if not skill:
            raise ValueError(f"Unknown skill: {skill_name}")
        if skill.roles and role not in skill.roles:
            raise ValueError(f"Skill '{skill_name}' not available for role '{role}'")

        prompt = skill.build_prompt(variables or {})

        if supervised:
            result = await self.supervised_send(role, prompt, cwd=cwd)
            return result.raw_response
        else:
            return await self.send_to_role(role, prompt, cwd=cwd)

    # ── HITL ────────────────────────────────────────────────────────────

    async def request_human_approval(
        self,
        agent_id: str,
        action: str,
        context: str,
        risk_level: str = "medium",
    ) -> bool:
        """Request human approval. Returns True if approved."""
        import uuid
        req = await self.hitl.request_approval(
            request_id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            role=self.registry.get_agent(agent_id).role.name if self.registry.get_agent(agent_id) else "unknown",
            action=action,
            context=context,
            risk_level=risk_level,
        )
        return req.status.value == "approved"

    # ── Event stream ────────────────────────────────────────────────────

    async def wait_for_shutdown(self):
        """Block until shutdown is requested."""
        await self._shutdown_event.wait()
