"""Workflow execution engine."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from orchestrator.agents.base import Agent
from orchestrator.agents.registry import AgentRegistry
from orchestrator.events.bus import EventBus
from orchestrator.state.store import StateStore, WorkflowRunRecord
from orchestrator.workflows.steps import Step, StepResult

logger = logging.getLogger(__name__)


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
        agents = self.registry.get_agents_by_role(role_name)
        running = [a for a in agents if a.status.value == "running"]
        return running[0] if running else None

    def get_or_create_thread(self, agent: Agent, step_id: str) -> str | None:
        key = f"{agent.agent_id}:{step_id}"
        return self._threads.get(key)

    def set_thread(self, agent: Agent, step_id: str, thread_id: str):
        key = f"{agent.agent_id}:{step_id}"
        self._threads[key] = thread_id


@dataclass
class Workflow:
    """A named, ordered sequence of steps with optional branching."""

    name: str
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    initial_variables: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 20  # safety limit for loops

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
        self._active_runs[rid] = ctx

        # Persist workflow run
        record = WorkflowRunRecord(
            run_id=rid,
            workflow_name=workflow.name,
            status="running",
            config={"variables": merged_vars},
        )
        await self._store.save_workflow_run(record)

        await self._bus.publish("workflow.started", {
            "run_id": rid, "workflow": workflow.name,
        }, source="engine")

        step_results: list[tuple[str, StepResult]] = []
        error: str | None = None

        try:
            if not workflow.steps:
                return WorkflowResult(run_id=rid, success=True, outputs=ctx.variables)

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

                result = await step.execute(ctx)
                step_results.append((step.step_id, result))
                ctx.variables.update(result.output)

                # Persist step result
                record.state = {
                    "current_step": step.step_id,
                    "iteration": iterations,
                    "variables": ctx.variables,
                }
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
