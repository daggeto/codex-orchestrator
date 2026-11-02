"""Workflow execution engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
    _threads: dict[str, str] = field(default_factory=dict)  # "role:backend:step_id" -> thread_id
    _attached: set[str] = field(default_factory=set)

    async def persist(self):
        record = await self.store.get_workflow_run(self.run_id)
        if record and record.state.get("checkpoint"):
            record.state["checkpoint"].update(variables=self.variables, threads=self._threads)
            await self.store.save_workflow_run(record)

    async def acquire_thread(self, agent, step_id, cwd=None, model=None):
        # Role identity survives replacement of a stopped backend process.
        key = f"{agent.role.name}:{agent.role.backend}:{step_id}"
        thread = self._threads.get(key)
        if thread and key not in self._attached:
            if agent.role.backend != "codex" and agent._get_thread(thread) is None:
                raise ValueError("This backend cannot safely restore the saved conversation after restart")
            info = await agent.resume_thread(thread)
            info.cwd = cwd
        elif not thread:
            info = await agent.start_thread(cwd=cwd, model=model)
            thread = info.thread_id
            self._threads[key] = thread
        self._attached.add(key)
        await self.persist()
        return thread

    def get_agent_for_role(self, role_name: str) -> Agent | None:
        agents = self.registry.get_agents_by_role(role_name, self.variables.get("task_id"))
        running = [a for a in agents if a.status.value == "running"]
        return running[0] if running else None

    def get_or_create_thread(self, agent: Agent, step_id: str) -> str | None:
        key = f"{agent.role.name}:{agent.role.backend}:{step_id}"
        return self._threads.get(key)

    def set_thread(self, agent: Agent, step_id: str, thread_id: str):
        key = f"{agent.role.name}:{agent.role.backend}:{step_id}"
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
    max_iterations: int = 20  # initial step budget; extensions require approval

    publication_policy: str = "none"
    review_policy: str | None = None
    role_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)

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

    @property
    def signature(self) -> str:
        def describe(value):
            if isinstance(value, Step):
                # New optional handoff/report behavior must not invalidate saved
                # checkpoints for workflows whose routing has not changed.
                options = {k: v for k, v in vars(value).items()
                           if not ((k == "next_step" and v is None)
                                   or (k in {"stable_snapshot", "advisory"} and v is False))}
                return {"type": type(value).__name__, "options": describe(options)}
            if isinstance(value, dict):
                return {k: describe(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [describe(v) for v in value]
            return value
        definition = describe(self.steps)
        if self.role_overrides:
            definition = {"steps": definition, "role_overrides": self.role_overrides}
        return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


def continuation_checkpoint(workflow, record):
    """Validate saved progress against the current workflow, allowing definition changes."""
    checkpoint = record.state.get("checkpoint")
    if checkpoint is None:
        # Old failed runs persisted outputs after each step, but not threads.
        # Interrupted old runs have no authoritative last successful boundary.
        step = record.state.get("current_step")
        saved = record.state.get("final_variables") or record.state.get("variables")
        if (record.status != "failed" or not step or not isinstance(saved, dict)
                or not str(record.state.get("error", "")).startswith(f"Step {step} failed:")):
            raise ValueError("This older run has no reliable checkpoint; restart it instead")
        if step.startswith("prepare"):
            raise ValueError("This older run stopped during preparation; inspect its files and restart")
        checkpoint = {"next_step": step, "variables": saved, "threads": {},
                      "iteration": max(0, record.state.get("iteration", 1) - 1)}
    checkpoint = dict(checkpoint)
    if not isinstance(checkpoint.get("variables"), dict):
        raise ValueError("The saved checkpoint has no workflow variables")
    policy_changed = workflow.review_policy and record.config.get("review_policy") != workflow.review_policy
    if policy_changed and checkpoint.get("review_policy") != workflow.review_policy:
        # The reviewer-only transition preserves implementation progress, but
        # approval from an older protocol cannot authorize completion/publication.
        if record.config.get("review_policy") is not None or workflow.review_policy != "reviewer-v1":
            raise ValueError("No checkpoint migration is available for these review rules")
        review = next((s for s in workflow.steps if getattr(s, "role", None) == "reviewer"
                       and not getattr(s, "advisory", False)), None)
        if review is None:
            raise ValueError("The current workflow has no reviewer step for saved work")
        saved_step = workflow.get_step(checkpoint.get("next_step"))
        if saved_step is None and checkpoint.get("next_step") not in {
            "review-gate", "ux-review", "ux-review-gate", "re-review-gate", "repeat-reviews", "__end__",
        }:
            raise ValueError("The saved step is unavailable in this workflow")
        if saved_step is None or workflow.steps.index(saved_step) >= workflow.steps.index(review):
            checkpoint["next_step"] = review.step_id
        variables = dict(checkpoint["variables"])
        for key in list(variables):
            if key.startswith(("review_", "ux_review_")) or key in {
                "completion_reviewed_sha", "ux-consult_commit", "architect-consult_commit",
            }:
                variables.pop(key)
        checkpoint["variables"] = {**workflow.initial_variables, **variables}
        # Keep implementation conversations; a fresh reviewer conversation uses
        # the current protocol and saved plans rather than obsolete instructions.
        checkpoint["threads"] = {key: value for key, value in checkpoint.get("threads", {}).items()
                                 if not key.startswith("reviewer:")}
        checkpoint["review_policy"] = workflow.review_policy
    step = checkpoint.get("next_step")
    if step != "__end__" and not workflow.get_step(step):
        raise ValueError("The saved step is unavailable in this workflow")
    return checkpoint


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
        *, accepted: bool = False, continuing: bool = False,
    ) -> WorkflowResult:
        if continuing and not accepted:
            raise ValueError("Continuation requires an accepted, reserved run")
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
        if continuing:
            checkpoint = continuation_checkpoint(workflow, record)
            ctx.variables = dict(checkpoint["variables"])
            ctx._threads = dict(checkpoint.get("threads", {}))
        else:
            checkpoint = {"next_step": workflow.steps[0].step_id if workflow.steps else "__end__",
                          "iteration": 0, "variables": ctx.variables, "threads": {}}
        record.config["workflow_signature"] = workflow.signature
        record.config["review_policy"] = workflow.review_policy
        record.state["checkpoint"] = checkpoint
        record.status = "running"
        record.completed_at = None
        record.state.update(phase="executing", required_roles=workflow.required_roles)
        await self._store.save_workflow_run(record)
        self._active_runs[rid] = ctx

        step_results: list[tuple[str, StepResult]] = []
        error: str | None = None
        paused = False
        iteration_limit = record.config.setdefault("iteration_limit", workflow.max_iterations)

        try:
            await self._bus.publish("workflow.started", {
                "run_id": rid, "workflow": workflow.name,
            }, source="engine")
            # Execute steps with branching support
            next_step = checkpoint["next_step"]
            current_idx = (len(workflow.steps) if next_step == "__end__" else
                           workflow.steps.index(workflow.get_step(next_step)))
            iterations = checkpoint["iteration"]

            while current_idx < len(workflow.steps) and iterations < iteration_limit:
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

                if result.success:
                    if result.next_step == "__end__":
                        current_idx = len(workflow.steps)
                    elif result.next_step:
                        target = workflow.get_step(result.next_step)
                        if target is None:
                            error = f"Branch target '{result.next_step}' not found"
                        else:
                            current_idx = workflow.steps.index(target)
                    else:
                        current_idx += 1
                else:
                    error = f"Step {step.step_id} failed: {result.error}"

                # Outputs and the next execution boundary are one durable write.
                # Never mark a successful step saved while still pointing at it.
                record.state.update(current_step=step.step_id, iteration=iterations,
                                    variables=ctx.variables)
                record.state["checkpoint"].update(
                    next_step=(step.step_id if error else
                               workflow.steps[current_idx].step_id if current_idx < len(workflow.steps) else "__end__"),
                    iteration=iterations - 1 if error else iterations,
                    variables=ctx.variables, threads=ctx._threads,
                )
                await self._store.save_workflow_run(record)
                await self._bus.publish("workflow.step.completed", {
                    "run_id": rid, "step_id": step.step_id,
                    "success": result.success, "output_keys": list(result.output.keys()),
                }, source="engine")
                if error:
                    logger.error("Workflow %s: %s", rid[:8], error)
                    break

            if not error and iterations >= iteration_limit and current_idx < len(workflow.steps):
                paused = True
                error = (f"Step budget reached ({iteration_limit}). "
                         "Allow 20 more steps to continue from the saved checkpoint.")

        except asyncio.CancelledError:
            await ctx.persist()
            record = await self._store.get_workflow_run(rid) or record
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
        await ctx.persist()
        record = await self._store.get_workflow_run(rid) or record
        record.status = "paused" if paused else "completed" if success else "failed"
        record.state.update(phase="waiting_for_step_budget" if paused else record.status, error=error)
        record.completed_at = None if paused else datetime.now(timezone.utc).isoformat()
        record.state["final_variables"] = ctx.variables
        await self._store.save_workflow_run(record)

        await self._bus.publish("workflow.paused" if paused else "workflow.completed", {
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
