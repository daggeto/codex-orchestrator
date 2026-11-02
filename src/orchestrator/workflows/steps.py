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
    pause_reason: str | None = None


class Step(ABC):
    """Base class for workflow steps."""

    def __init__(self, step_id: str, name: str = "", **kwargs):
        self.step_id = step_id
        self.name = name or step_id

    @abstractmethod
    async def execute(self, ctx: WorkflowContext) -> StepResult:
        ...


async def task_snapshot(
    ctx: WorkflowContext, *, require_clean: bool = True, committed_only: bool = False,
) -> str | None:
    """Identify committed source or, for planning, the actual checkout contents.

    Explicit committed-source observations exclude working edits. The default
    retains the clean-checkout guard; planning fingerprints actual file contents
    with require_clean=False.
    """
    root = ctx.variables.get("task_worktree")
    if not root:
        return None
    from orchestrator.workflows.merge_request import _run
    if await _run(root, "git", "branch", "--show-current") != ctx.variables["task_branch"]:
        raise ValueError("Task branch changed; restore the selected checkout before continuing")
    if committed_only:
        return await _run(root, "git", "rev-parse", "HEAD")
    status = await _run(root, "git", "status", "--porcelain", "--untracked-files=all")
    if status and require_clean:
        raise ValueError("Uncommitted changes remain; finish implementation and commit task changes before review. Preserve unrelated edits.")
    head = await _run(root, "git", "rev-parse", "HEAD")
    if require_clean:
        return head
    # Planning can read an existing dirty checkout. Bind approval to its actual
    # contents without staging, committing, or discarding the developer's edits.
    import hashlib
    snapshot = hashlib.sha256()
    for value in (head, status, await _run(root, "git", "diff", "HEAD", "--binary", "--no-ext-diff")):
        snapshot.update(value.encode())
        snapshot.update(b"\0")
    untracked = await _run(root, "git", "ls-files", "--others", "--exclude-standard", "-z")
    for path in filter(None, untracked.split("\0")):
        snapshot.update(path.encode())
        snapshot.update((await _run(root, "git", "hash-object", "--", path)).encode())
        snapshot.update(b"\0")
    return snapshot.hexdigest()


def committed_source_instructions(commit: str) -> str:
    return (
        f"\n\nSource evidence is bound to commit {commit}. The selected checkout may "
        "contain unrelated staged, unstaged, or untracked edits. Preserve them. "
        "Inspect committed content using git show <commit>:<path> and git diff "
        "<baseline> <commit>; reading working files can include unrelated edits. "
        "Exclude uncommitted edits from source approval and report any requested "
        "implementation missing from the committed revision. Do not stage, commit, "
        "stash, reset, or discard unrelated edits."
    )


def reviewer_criteria(variables) -> str:
    """Every reviewer receives the same planning evidence, including repair reviews."""
    sections = [
        "You own source approval. Verify the complete implementation against the user's request "
        "and every applicable UX and architecture acceptance criterion. Identify omissions and "
        "unapproved deviations with file/line evidence and concrete required fixes. Send those "
        "findings directly to the implementer; approve when they are resolved. Specialists advise "
        "on plans and tradeoffs; they do not grant approval. Do not invent missing planning stages."
    ]
    for key, label in (("ux_assessment", "UX plan"), ("plan", "Architecture plan"),
                       ("investigation", "Bug investigation"), ("ux_consultation", "UX advice"),
                       ("architecture_consultation", "Architecture advice")):
        if variables.get(key):
            sections.append(label + ":\n" + str(variables[key]))
    if variables.get("approved_plan"):
        sections.append("Developer-approved plan (supersedes historical drafts):\n" + variables["approved_plan"])
    return "\n\n" + "\n\n".join(sections)


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
        next_step: str | None = None,
        stable_snapshot: bool = False,
        thread_key: str | None = None,
        **kwargs,
    ):
        super().__init__(step_id, **kwargs)
        self.role = role
        self.message_template = message_template
        self.cwd = cwd
        self.model = model
        self.output_key = output_key
        self.next_step = next_step
        self.stable_snapshot = stable_snapshot
        if thread_key is not None:
            self.thread_key = thread_key

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        if self.role == 'implementer' and ctx.variables.get('_requires_plan_approval'):
            approval = ctx.variables.get('plan_approval') or {}
            if approval.get('status') != 'approved' or not ctx.variables.get('approved_plan'):
                return StepResult(False, error='Developer plan approval is required before implementation')
        agent = ctx.get_agent_for_role(self.role)
        if not agent:
            return StepResult(success=False, error=f"No running agent with role '{self.role}'")

        message = self.message_template.format(**ctx.variables)
        if ctx.variables.get('_recovery_capabilities'):
            message += '\nIncident-local installed capabilities (use documented absolute paths; do not modify global configuration):\n' + ctx.variables['_recovery_capabilities']
        if ctx.variables.get("planning_feedback") and self.step_id in ctx.variables.get("planning_targets", []):
            message += "\nDeveloper feedback and current draft (preserve developer decisions):\n" + ctx.variables["planning_feedback"]
        if ctx.variables.get("approved_plan") and self.role in {"implementer", "reviewer"}:
            message += "\nThe approved plan below supersedes historical planning outputs. Preserve the original request:\n" + ctx.variables["approved_plan"]
        if self.role == "implementer" and ctx.variables.get("task_worktree"):
            message += (
                "\n\nThe task has selected this checkout: "
                f"{ctx.variables['task_worktree']} on branch {ctx.variables['task_branch']}. "
                "Reuse this checkout and branch for all edits and commits. Do not create "
                "another worktree or switch branches; review and publication use this checkout. "
                "Finish and commit the task changes before handoff. Preserve unrelated edits; "
                "they do not need to be committed or removed for source review. Review covers "
                "committed content only, so uncommitted task changes cannot receive approval."
            )
        if ctx.variables.get('workspace_kind') == 'main':
            message += "\n\nThe task explicitly selected execution in this main checkout. Use this folder and its current branch for implementation."
        cwd = ctx.variables.get("cwd") if ctx.variables.get("workspace_resolved") else (self.cwd.format(**ctx.variables) if self.cwd else ctx.variables.get("cwd"))

        try:
            before = await task_snapshot(ctx, committed_only=True) if self.stable_snapshot else None
        except (OSError, RuntimeError, ValueError) as exc:
            return StepResult(success=False, error=str(exc))
        if before:
            message += committed_source_instructions(before)
        thread = await ctx.acquire_thread(agent, getattr(self, 'thread_key', None) or self.step_id, cwd=cwd, model=self.model)

        await ctx.persist_step_thread(agent, thread)
        result = await agent.send_message(thread, message, model=self.model)
        if not result.ok:
            return StepResult(success=False, error=result.error or "Agent turn did not complete")

        identity = {}
        if (self.role == "implementer" or self.stable_snapshot) and ctx.variables.get("task_worktree"):
            from orchestrator.workflows.merge_request import _run
            try:
                root = ctx.variables["task_worktree"]
                head = await task_snapshot(ctx, committed_only=True)
                if self.stable_snapshot and head != before:
                    raise ValueError("Task commit changed during consultation; use a stable checkout")
                if self.role == "implementer":
                    identity["task_commit"] = head
                await _run(root, "git", "merge-base", "--is-ancestor",
                           ctx.variables["task_baseline"], head)
            except (OSError, RuntimeError, ValueError) as exc:
                return StepResult(success=False, error=f"Invalid task worktree: {exc}")

        if (self.role == "implementer" and identity.get("task_commit")
                and ctx.variables.get("review_decision") == "changes_requested"
                and identity["task_commit"] == ctx.variables.get("review_commit")):
            return StepResult(success=False, output={**identity, self.output_key: result.full_text},
                              error="Revision produced no new commit for the review findings. Inspect the implementer report before continuing.")

        return StepResult(
            success=result.ok,
            output={
                **identity,
                self.output_key: result.full_text,
                f"{self.output_key}_files": result.files_changed,
                f"{self.output_key}_commands": result.commands_run,
            },
            error=result.error,
            next_step=self.next_step,
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
        advisory: bool = False,
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
        self.advisory = advisory
        if thread_key is not None:
            self.thread_key = thread_key

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        agent = ctx.get_agent_for_role(self.role)
        if not agent:
            return StepResult(success=False, error=f"No running agent with role '{self.role}'")

        source = ctx.variables.get(self.source_key, "")
        message = self.review_template.format(**{**ctx.variables, "source": source})
        if self.role == "reviewer":
            message += reviewer_criteria(ctx.variables)

        thread = await ctx.acquire_thread(agent, getattr(self, 'thread_key', None) or self.step_id, cwd=ctx.variables.get("cwd"))

        reviewed = {}
        if ctx.variables.get("task_worktree"):
            try:
                head = await task_snapshot(ctx, committed_only=True)
                if ctx.variables.get("task_commit", head) != head:
                    raise ValueError("Task commit changed since implementation handoff; finish implementation before review")
                reviewed[f"{self.output_namespace}_commit"] = head
                message += committed_source_instructions(head)
            except (OSError, RuntimeError, ValueError) as exc:
                return StepResult(success=False, error=str(exc))
        await ctx.persist_step_thread(agent, thread)
        result = await agent.send_message(thread, message)
        if reviewed:
            try:
                if await task_snapshot(ctx, committed_only=True) != reviewed[f"{self.output_namespace}_commit"]:
                    return StepResult(success=False, error="Task commit changed during review")
            except (OSError, RuntimeError, ValueError) as exc:
                return StepResult(success=False, error=str(exc))
        if not result.ok:
            return StepResult(success=False, error=result.error or "Review failed")
        if self.advisory:
            return StepResult(success=True, output={
                **reviewed,
                f"{self.output_namespace}_status": "reported" if result.full_text.strip() else "unavailable",
                f"{self.output_namespace}_text": result.full_text.strip() or
                    "No final UX report was returned. UX assessment remains outstanding follow-up work.",
            }, next_step=self.on_approve)
        text = result.full_text.upper()

        approved = self.approve_keyword.upper() in text
        rejected = self.reject_keyword.upper() in text

        if self.strict_decision:
            # Only standalone decision lines are markers; ordinary prose may
            # mention approval (for example, "developer-approved plan").
            # Still reject conflicting standalone markers in the same response.
            markers = set(re.findall(
                rf"^[ \t]*({re.escape(self.approve_keyword.upper())}|{re.escape(self.reject_keyword.upper())})[ \t]*\r?$",
                text,
                re.MULTILINE,
            ))
            final = text.strip().splitlines()[-1].strip() if text.strip() else ""
            approved = markers == {self.approve_keyword.upper()} and final == self.approve_keyword.upper()
            rejected = markers == {self.reject_keyword.upper()} and final == self.reject_keyword.upper()

        decision = "approved" if approved and not rejected else "changes_requested" if rejected else "unclear"
        output = {
            **(reviewed if decision == "approved" or self.strict_decision else {}),
            f"{self.output_namespace}_decision": decision,
            f"{self.output_namespace}_text": result.full_text,
        }
        final = result.full_text.strip().splitlines()[-1] if result.full_text.strip() else ""
        if decision == "unclear" and final not in getattr(self, "consultation_routes", {}):
            return StepResult(success=False, output=output,
                              error="Reviewer returned no clear decision. End with APPROVED or CHANGES_REQUESTED; no implementation revision was requested.")
        return StepResult(
            success=True,
            output=output,
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
