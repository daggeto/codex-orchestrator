"""Bounded local changes with one combined review and one repair cycle."""
from __future__ import annotations

import re

from orchestrator.workflows.steps import AssignStep, Step, StepResult


SMALL_CHANGE_INSTRUCTIONS = """Inspect only the request's direct dependencies, then make the smallest safe change.
Reuse existing UI patterns and contracts. Do not add unrelated hardening or refactoring.
Before editing, if this needs a schema migration, a new background service or recovery
system, a substantial public contract change, or more than five implementation files,
stop. Explain the blocker, the smallest safe alternative, and the larger proposal.
End that response with a standalone SCOPE_ESCALATION line. Never silently expand scope.
Follow repository validation policy. Add or run tests only when authorized; report
unperformed checks and concise manual acceptance steps. Do not start testing loops.
Use targeted reads and concise deliverables; avoid repeating source dumps or plans.
Commit only task changes in the selected checkout. Do not push, publish, or merge.
If implemented, report the commit, affected files, checks actually run, and pending
validation, then end with a standalone SMALL_CHANGE_READY line."""


class SmallChangeAssignment(AssignStep):
    """An escalation or missing completion marker cannot proceed into review."""

    async def execute(self, ctx):
        result = await super().execute(ctx)
        if not result.success:
            return result
        text = result.output[self.output_key]
        escalated = re.search(r"(?m)^SCOPE_ESCALATION\s*$", text)
        ready = text.strip().splitlines()[-1:] == ["SMALL_CHANGE_READY"]
        if escalated or not ready:
            result.output["checkpoint_reason"] = (
                "Scope exceeds small-change; submit a narrower task or explicitly select feature."
                if escalated else "Implementation did not confirm completion; inspect its report before restarting."
            )
            result.next_step = "checkpoint"
        return result


class SmallChangeCheckpoint(Step):
    """Stop without interpreting a request for more steps as permission to expand."""

    async def execute(self, ctx):
        reason = ctx.variables.get("checkpoint_reason") or (
            "The single revision cycle is exhausted or review is unclear. "
            "Inspect the remaining findings and explicitly submit follow-up work."
        )
        return StepResult(success=False, output={"summary": reason}, error=reason)


class SmallChangeSummary(Step):
    """Use existing deliverables instead of invoking another model to summarize."""

    async def execute(self, ctx):
        report = ctx.variables["implementation"]
        report = report.removesuffix("SMALL_CHANGE_READY").rstrip()
        return StepResult(success=True, output={
            "summary": report + "\n\nSource review approved. Publication disabled. "
            "Browser acceptance remains pending.\nFinal validation: " + ctx.variables.get("validation_summary", "Not performed")
        }, next_step="__end__")
