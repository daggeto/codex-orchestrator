"""Workflow execution engine."""

from __future__ import annotations

import asyncio
import logging
import uuid
import contextvars
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from orchestrator.agents.base import Agent
from orchestrator.agents.registry import AgentRegistry
from orchestrator.events.bus import EventBus
from orchestrator.state.store import StateStore, WorkflowRunRecord
from orchestrator.workflows.steps import Step, StepResult

logger = logging.getLogger(__name__)
_execution = contextvars.ContextVar("step_execution", default=None)


@dataclass
class WorkflowContext:
    """Shared context passed to every step in a workflow."""

    run_id: str
    registry: AgentRegistry
    store: StateStore
    bus: EventBus
    variables: dict[str, Any] = field(default_factory=dict)
    _threads: dict[str, str] = field(default_factory=dict)  # "agent_id:step_id" -> thread_id

    def get_agent_for_role(self, role_name: str) -> Agent | None:
        agents = self.registry.get_agents_by_role(role_name, self.variables.get("task_id"))
        running = [a for a in agents if a.status.value == "running"]
        return running[0] if running else None

    def get_or_create_thread(self, agent: Agent, step_id: str) -> str | None:
        key = f"{agent.agent_id}:{step_id}"
        return self._threads.get(key)

    def set_thread(self, agent: Agent, step_id: str, thread_id: str):
        key = f"{agent.agent_id}:{step_id}"
        self._threads[key] = thread_id

    async def execute_step(self, step):
        from orchestrator.state.models import now
        eid = uuid.uuid4().hex
        parent = _execution.get()
        attempts = await self.store.one('SELECT COUNT(*) AS count FROM step_executions WHERE run_id=? AND step_id=?', (self.run_id, step.step_id))
        task_id = self.variables.get('task_id')
        agent = self.get_agent_for_role(step.role) if getattr(step, 'role', None) else None
        dependencies = [self.variables['_last_execution']] if not parent and self.variables.get('_last_execution') else []
        await self.store.write('INSERT INTO step_executions (execution_id,task_id,run_id,step_id,attempt,parent_id,dependencies_json,agent_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (eid, task_id, self.run_id, step.step_id, attempts['count']+1, parent, json.dumps(dependencies), agent.agent_id if agent else None, 'active', now(), now()))
        if task_id:
            await self.store.task_status(task_id, 'active', step.name + (' running' if agent else ''))
        token = _execution.set(eid)
        try:
            result = await step.execute(self)
            await self.store.write('UPDATE step_executions SET status=?,output_json=?,updated_at=? WHERE execution_id=?',
                                   ('completed' if result.success else 'failed', json.dumps({'output': result.output, 'error': result.error, 'next_step': result.next_step}), now(), eid))
            return result
        except BaseException as exc:
            await self.store.write('UPDATE step_executions SET status=?,output_json=?,updated_at=? WHERE execution_id=?',
                                   ('interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed', json.dumps({'error': str(exc)}), now(), eid))
            raise
        finally:
            _execution.reset(token)
            if not parent:
                self.variables['_last_execution'] = eid

    async def persist_step_thread(self, agent, thread_id):
        from orchestrator.state.store import ThreadRecord
        info = agent._get_thread(thread_id)
        await self.store.save_thread(ThreadRecord(thread_id=thread_id, agent_id=agent.agent_id,
                                                 cwd=info.cwd if info else None, model=agent.role.model))
        await self.store.write('UPDATE threads SET task_id=?,step_execution_id=? WHERE thread_id=?',
                               (self.variables.get('task_id'), _execution.get(), thread_id))
        await self.store.write('UPDATE step_executions SET thread_id=? WHERE execution_id=?', (thread_id, _execution.get()))


@dataclass
class Workflow:
    """A named, ordered sequence of steps with optional branching."""

    name: str
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    initial_variables: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 20  # safety limit for loops

    publication_policy: str = "none"

    @property
    def required_roles(self) -> list[str]:
        """Collect roles from assignments, reviews, and nested steps."""
        roles: list[str] = []

        def collect(steps):
            for step in steps:
                role = getattr(step, "role", None)
                if role and role not in roles:
                    roles.append(role)
                collect(getattr(step, "sub_steps", []))

        collect(self.steps)
        return roles

    def add_step(self, step: Step) -> Workflow:
        self.steps.append(step)
        return self

    def get_step(self, step_id: str) -> Step | None:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None


class WorkflowEngine:
    """Executes workflows using the agent registry."""

    def __init__(self, registry: AgentRegistry, store: StateStore, bus: EventBus):
        self._registry = registry
        self._store = store
        self._bus = bus
        self._active_runs: dict[str, WorkflowContext] = {}

    async def run(
        self,
        workflow: Workflow,
        variables: dict[str, Any] | None = None,
        run_id: str | None = None,
        *, accepted: bool = False,
    ) -> WorkflowResult:
        rid = run_id or uuid.uuid4().hex[:16]
        merged_vars = {**workflow.initial_variables, **(variables or {})}

        ctx = WorkflowContext(
            run_id=rid,
            registry=self._registry,
            store=self._store,
            bus=self._bus,
            variables=merged_vars,
        )
        if rid in self._active_runs:
            raise ValueError("Workflow run is already active")
        if accepted:
            record = await self._store.get_workflow_run(rid)
            if not record or record.status != "accepted" or record.workflow_name != workflow.name:
                raise ValueError("No accepted submission for this run")
        else:
            record = WorkflowRunRecord(
                run_id=rid, workflow_name=workflow.name,
                config={"variables": merged_vars},
            )
            if not await self._store.reserve_workflow_run(record):
                raise ValueError("Workflow run ID already exists")
        record.status = "running"
        record.state.update(phase="executing", required_roles=workflow.required_roles)
        await self._store.save_workflow_run(record)
        self._active_runs[rid] = ctx

        step_results: list[tuple[str, StepResult]] = []
        error: str | None = None

        try:
            await self._bus.publish("workflow.started", {
                "run_id": rid, "workflow": workflow.name,
            }, source="engine")
            # Execute steps with branching support
            current_idx = 0
            iterations = 0

            while current_idx < len(workflow.steps) and iterations < workflow.max_iterations:
                iterations += 1
                step = workflow.steps[current_idx]

                logger.info("Workflow %s: executing step %s (%s)", rid[:8], step.step_id, step.name)
                await self._bus.publish("workflow.step.started", {
                    "run_id": rid, "step_id": step.step_id,
                }, source="engine")

                record.state.update(current_step=step.step_id, iteration=iterations)
                await self._store.save_workflow_run(record)
                result = await ctx.execute_step(step)
                record = await self._store.get_workflow_run(rid) or record
                step_results.append((step.step_id, result))
                ctx.variables.update(result.output)

                # Persist step result
                record.state.update({
                    "current_step": step.step_id,
                    "iteration": iterations,
                    "variables": ctx.variables,
                })
                await self._store.save_workflow_run(record)

                await self._bus.publish("workflow.step.completed", {
                    "run_id": rid, "step_id": step.step_id,
                    "success": result.success, "output_keys": list(result.output.keys()),
                }, source="engine")

                if not result.success:
                    error = f"Step {step.step_id} failed: {result.error}"
                    logger.error("Workflow %s: %s", rid[:8], error)
                    break

                # Handle branching
                if result.next_step:
                    target = workflow.get_step(result.next_step)
                    if target:
                        target_idx = workflow.steps.index(target)
                        current_idx = target_idx
                        logger.info("Workflow %s: branching to step %s", rid[:8], result.next_step)
                    else:
                        if result.next_step == "__end__":
                            break
                        error = f"Branch target '{result.next_step}' not found"
                        break
                else:
                    current_idx += 1

            if iterations >= workflow.max_iterations:
                error = f"Workflow exceeded max iterations ({workflow.max_iterations})"

        except asyncio.CancelledError:
            record.status = "interrupted"
            record.state.update(phase="interrupted", error="Service stopped during execution")
            record.state["final_variables"] = ctx.variables
            await self._store.save_workflow_run(record)
            await self._bus.publish("workflow.interrupted", {"run_id": rid}, source="engine")
            raise
        except Exception as e:
            error = str(e)
            logger.exception("Workflow %s failed", rid[:8])

        finally:
            del self._active_runs[rid]

        success = error is None
        record.status = "completed" if success else "failed"
        record.state.update(phase=record.status, error=error)
        record.completed_at = datetime.now(timezone.utc).isoformat()
        record.state["final_variables"] = ctx.variables
        await self._store.save_workflow_run(record)

        await self._bus.publish("workflow.completed", {
            "run_id": rid, "workflow": workflow.name,
            "success": success, "error": error,
        }, source="engine")

        return WorkflowResult(
            run_id=rid,
            success=success,
            outputs=ctx.variables,
            step_results=step_results,
            error=error,
        )


@dataclass
class WorkflowResult:
    run_id: str
    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    step_results: list[tuple[str, StepResult]] = field(default_factory=list)
    error: str | None = None
