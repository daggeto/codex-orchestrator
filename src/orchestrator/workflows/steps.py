"""Workflow step types — the building blocks of workflows."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.workflows.engine import WorkflowContext

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    next_step: str | None = None  # override the default next step
    error: str | None = None


class Step(ABC):
    """Base class for workflow steps."""

    def __init__(self, step_id: str, name: str = "", **kwargs):
        self.step_id = step_id
        self.name = name or step_id

    @abstractmethod
    async def execute(self, ctx: WorkflowContext) -> StepResult:
        ...


class AssignStep(Step):
    """Assign a task to an agent and collect the response."""

    def __init__(
        self,
        step_id: str,
        role: str,
        message_template: str,
        cwd: str | None = None,
        model: str | None = None,
        output_key: str = "response",
        thread_key: str | None = None,
        **kwargs,
    ):
        super().__init__(step_id, **kwargs)
        self.role = role
        self.message_template = message_template
        self.cwd = cwd
        self.model = model
        self.output_key = output_key
        if thread_key is not None:
            self.thread_key = thread_key

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        agent = ctx.get_agent_for_role(self.role)
        if not agent:
            return StepResult(success=False, error=f"No running agent with role '{self.role}'")

        message = self.message_template.format(**ctx.variables)
        if self.role == "implementer" and ctx.variables.get("task_worktree"):
            message += (
                "\n\nThe task has selected this checkout: "
                f"{ctx.variables['task_worktree']} on branch {ctx.variables['task_branch']}. "
                "Reuse this checkout and branch for all edits and commits. Do not create "
                "another worktree or switch branches; review and publication use this checkout."
            )
        if ctx.variables.get('workspace_kind') == 'main':
            message += "\n\nThe task explicitly selected execution in this main checkout. Use this folder and its current branch for implementation."
        cwd = ctx.variables.get("cwd") if ctx.variables.get("workspace_resolved") else (self.cwd.format(**ctx.variables) if self.cwd else ctx.variables.get("cwd"))

        thread = await ctx.acquire_thread(agent, getattr(self, 'thread_key', None) or self.step_id, cwd=cwd, model=self.model)

        await ctx.persist_step_thread(agent, thread)
        result = await agent.send_message(thread, message, model=self.model)

        identity = {}
        if self.role == "implementer" and ctx.variables.get("task_worktree"):
            from orchestrator.workflows.merge_request import _run
            try:
                root = ctx.variables["task_worktree"]
                if await _run(root, "git", "branch", "--show-current") != ctx.variables["task_branch"]:
                    raise ValueError("Implementer changed the workflow task branch")
                identity["task_commit"] = await _run(root, "git", "rev-parse", "HEAD")
                await _run(root, "git", "merge-base", "--is-ancestor",
                           ctx.variables["task_baseline"], identity["task_commit"])
            except (OSError, RuntimeError, ValueError) as exc:
                return StepResult(success=False, error=f"Invalid task worktree: {exc}")

        return StepResult(
            success=result.ok,
            output={
                **identity,
                self.output_key: result.full_text,
                f"{self.output_key}_files": result.files_changed,
                f"{self.output_key}_commands": result.commands_run,
            },
            error=result.error,
        )


class ReviewStep(Step):
    """Send output from a previous step to a reviewer agent."""

    def __init__(
        self,
        step_id: str,
        role: str,
        review_template: str,
        source_key: str = "response",
        approve_keyword: str = "APPROVED",
        reject_keyword: str = "CHANGES_REQUESTED",
        on_approve: str | None = None,
        on_reject: str | None = None,
        output_namespace: str = "review",
        strict_decision: bool = False,
        thread_key: str | None = None,
        **kwargs,
    ):
        super().__init__(step_id, **kwargs)
        self.role = role
        self.review_template = review_template
        self.source_key = source_key
        self.approve_keyword = approve_keyword
        self.reject_keyword = reject_keyword
        self.on_approve = on_approve
        self.on_reject = on_reject
        self.output_namespace = output_namespace
        self.strict_decision = strict_decision
        if thread_key is not None:
            self.thread_key = thread_key

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        agent = ctx.get_agent_for_role(self.role)
        if not agent:
            return StepResult(success=False, error=f"No running agent with role '{self.role}'")

        source = ctx.variables.get(self.source_key, "")
        message = self.review_template.format(**{**ctx.variables, "source": source})

        thread = await ctx.acquire_thread(agent, getattr(self, 'thread_key', None) or self.step_id, cwd=ctx.variables.get("cwd"))

        reviewed = {}
        if ctx.variables.get("task_worktree"):
            from orchestrator.workflows.merge_request import _run
            try:
                root = ctx.variables["task_worktree"]
                if await _run(root, "git", "branch", "--show-current") != ctx.variables["task_branch"]:
                    raise ValueError("Task branch changed before review")
                reviewed[f"{self.output_namespace}_commit"] = await _run(root, "git", "rev-parse", "HEAD")
            except (OSError, RuntimeError, ValueError) as exc:
                return StepResult(success=False, error=str(exc))
        await ctx.persist_step_thread(agent, thread)
        result = await agent.send_message(thread, message)
        if reviewed:
            if await _run(root, "git", "rev-parse", "HEAD") != reviewed[f"{self.output_namespace}_commit"]:
                return StepResult(success=False, error="Task commit changed during review")
        if not result.ok:
            return StepResult(success=False, error=result.error or "Review failed")
        text = result.full_text.upper()

        approved = self.approve_keyword.upper() in text
        rejected = self.reject_keyword.upper() in text

        if self.strict_decision:
            # Require a final standalone marker and reject conflicting markers anywhere.
            markers = set(re.findall(
                rf"\b(?:{re.escape(self.approve_keyword.upper())}|{re.escape(self.reject_keyword.upper())})\b",
                text,
            ))
            final = text.strip().splitlines()[-1] if text.strip() else ""
            approved = markers == {self.approve_keyword.upper()} and final == self.approve_keyword.upper()
            rejected = markers == {self.reject_keyword.upper()} and final == self.reject_keyword.upper()

        decision = "approved" if approved and not rejected else "changes_requested" if rejected else "unclear"
        return StepResult(
            success=True,
            output={
                **(reviewed if decision == "approved" or self.strict_decision else {}),
                f"{self.output_namespace}_decision": decision,
                f"{self.output_namespace}_text": result.full_text,
            },
            next_step=self.on_approve if decision == "approved" else self.on_reject,
        )


class GateStep(Step):
    """Conditional routing based on a variable or expression."""

    def __init__(
        self,
        step_id: str,
        condition_key: str,
        expected_value: str | None = None,
        on_true: str | None = None,
        on_false: str | None = None,
        **kwargs,
    ):
        super().__init__(step_id, **kwargs)
        self.condition_key = condition_key
        self.expected_value = expected_value
        self.on_true = on_true
        self.on_false = on_false

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        value = ctx.variables.get(self.condition_key)

        if self.expected_value is not None:
            match = str(value) == self.expected_value
        else:
            match = bool(value)

        return StepResult(
            success=True,
            output={"gate_result": match, "gate_key": self.condition_key, "gate_value": value},
            next_step=self.on_true if match else self.on_false,
        )


class ParallelStep(Step):
    """Fan out work to multiple steps in parallel."""

    def __init__(
        self,
        step_id: str,
        sub_steps: list[Step],
        merge_key: str = "parallel_results",
        **kwargs,
    ):
        super().__init__(step_id, **kwargs)
        self.sub_steps = sub_steps
        self.merge_key = merge_key

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        import asyncio

        tasks = [ctx.execute_step(step) for step in self.sub_steps]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: dict[str, Any] = {}
        all_success = True
        errors: list[str] = []

        for step, result in zip(self.sub_steps, results):
            if isinstance(result, Exception):
                all_success = False
                errors.append(f"{step.step_id}: {result}")
                merged[step.step_id] = {"error": str(result)}
            else:
                if not result.success:
                    all_success = False
                    errors.append(f"{step.step_id}: {result.error}")
                merged[step.step_id] = result.output
                ctx.variables.update(result.output)

        return StepResult(
            success=all_success,
            output={self.merge_key: merged},
            error="; ".join(errors) if errors else None,
        )


class SequenceStep(Step):
    """Run a list of steps in order."""

    def __init__(self, step_id: str, sub_steps: list[Step], **kwargs):
        super().__init__(step_id, **kwargs)
        self.sub_steps = sub_steps

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        combined_output: dict[str, Any] = {}

        for step in self.sub_steps:
            result = await ctx.execute_step(step)
            combined_output.update(result.output)
            ctx.variables.update(result.output)
            if not result.success:
                return StepResult(
                    success=False,
                    output=combined_output,
                    error=f"Step {step.step_id} failed: {result.error}",
                )

        return StepResult(success=True, output=combined_output)


class HumanStep(Step):
    """Pause workflow and wait for human input via the event bus."""

    def __init__(
        self,
        step_id: str,
        prompt: str,
        output_key: str = "human_input",
        timeout: float = 3600,
        **kwargs,
    ):
        super().__init__(step_id, **kwargs)
        self.prompt = prompt
        self.output_key = output_key
        self.timeout = timeout

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        prompt = self.prompt.format(**ctx.variables)

        service = getattr(ctx.registry, 'task_service', None)
        if service:
            from orchestrator.workflows.engine import _execution
            try:
                response = await service.request_input(ctx.variables.get('task_id'), prompt,
                                                       execution_id=_execution.get(), timeout=self.timeout)
                return StepResult(success=True, output={self.output_key: response})
            except TimeoutError:
                return StepResult(success=False, error='Timed out waiting for human input')

        await ctx.bus.publish("workflow.human_input_needed", {
            "workflow_run_id": ctx.run_id,
            "step_id": self.step_id,
            "prompt": prompt,
        }, source="workflow")

        try:
            event = await ctx.bus.wait_for(
                f"workflow.human_input.{ctx.run_id}.{self.step_id}",
                timeout=self.timeout,
            )
            return StepResult(
                success=True,
                output={self.output_key: event.payload.get("input", "")},
            )
        except TimeoutError:
            return StepResult(success=False, error="Timed out waiting for human input")
