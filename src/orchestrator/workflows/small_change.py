"""Bounded local changes with reviewer explanations when repairs are exhausted."""
from __future__ import annotations

import re

from orchestrator.workflows.steps import AssignStep, Step, StepResult
from orchestrator.workflows.scope import SCOPE_PROPOSAL_INSTRUCTIONS, extract_proposals


MAX_SOURCE_REVISIONS = 10
REVISION_REVIEW_INSTRUCTIONS = (
    "\nSource revision round: {source_revision_count} of 10 (0 is the initial implementation). "
    "For any blocking finding, explain the concrete defect, developer impact, and required fix "
    "with file and line evidence. On round 10, if you cannot approve, include a concise "
    "explanation for the developer of why the issue remains unresolved after the revisions. "
    "Use the available revision history to distinguish recurring findings from newly found "
    "issues; do not invent causes when the evidence is insufficient. If the decision is "
    "unclear, explain what information prevents approval. This explanation will be shown "
    "to the developer when the workflow stops."
)


def source_revision_failure(ctx, decision_keys):
    """Surface the blocking agents' own findings in the saved developer report."""
    reports = []
    for key in decision_keys:
        if ctx.variables.get(key) == "approved":
            continue
        namespace = key.removesuffix("_decision")
        label = "UX reviewer (senior-ux-designer)" if namespace == "ux_review" else "Reviewer"
        feedback = ctx.variables.get(f"{namespace}_text", "").strip()
        decision = ctx.variables.get(key, "missing")
        reports.append(
            f"{label} ({decision}):\n" + (feedback or
            "No explanation was returned by this agent. Approval is missing; inspect its transcript.")
        )
    reason = (
        f"Review remains unresolved after {MAX_SOURCE_REVISIONS} revision rounds. "
        "The blocking agents reported:\n\n" + "\n\n".join(reports)
    )
    return StepResult(success=False, output={"summary": reason, "checkpoint_reason": reason}, error=reason)


class SmallChangeRevisionGate(Step):
    async def execute(self, ctx):
        count = ctx.variables.get("source_revision_count", 0)
        if count >= MAX_SOURCE_REVISIONS:
            return source_revision_failure(ctx, ["review_decision"])
        return StepResult(success=True, output={"source_revision_count": count + 1}, next_step="revise")


SMALL_CHANGE_INSTRUCTIONS = """Inspect only the request's direct dependencies, then make the smallest safe change.
Reuse existing UI patterns and contracts. Do not add unrelated hardening or refactoring.
Keep one clear outcome with straightforward acceptance criteria, a localized change
using existing patterns and contracts, and a diff one reviewer can assess comfortably.
More than five implementation files is a scope checkpoint, not a hard limit: explain
why the work remains small and continue if these conditions hold. File count alone
must not trigger escalation. Apply this guidance to the full task diff, including revisions.
Before editing, stop if this needs a schema migration, a new background service or
recovery system, a substantial public contract or architectural change, or broader
dependencies or separate design decisions that break the small-task conditions.
Explain the blocker, the smallest safe alternative, and the larger proposal.
End that response with a standalone SCOPE_ESCALATION line. Never silently expand scope.
Follow repository validation policy. Add or run tests only when authorized; report
unperformed checks and concise manual acceptance steps. Do not start testing loops.
Use targeted reads and concise deliverables; avoid repeating source dumps or plans.
Commit only task changes in the selected checkout. Do not push, publish, or merge.
If implemented, report the commit, affected files, checks actually run, and pending
validation, then end with a standalone SMALL_CHANGE_READY line.""" + "\n\n" + SCOPE_PROPOSAL_INSTRUCTIONS


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
            # Clear an earlier split before considering a new assignment report.
            result.output['scope_suggestions'] = []
            result.output['scope_suggestions_error'] = ''
            if escalated:
                record = await ctx.store.get_workflow_run(ctx.run_id)
                try:
                    result.output['scope_suggestions'] = extract_proposals(text, record.workflow_name)
                except (ValueError, TypeError, AttributeError) as exc:
                    result.output['scope_suggestions_error'] = str(exc)
            result.output["checkpoint_reason"] = (
                "This request exceeds the selected workflow. Review the proposed task split below."
                if escalated else "Implementation did not confirm completion; inspect its report before restarting."
            )
            result.next_step = "checkpoint"
        return result


class SmallChangeCheckpoint(Step):
    """Stop without interpreting a request for more steps as permission to expand."""

    async def execute(self, ctx):
        reason = ctx.variables.get("checkpoint_reason") or (
            "The task stopped without a completion report. Inspect the latest agent feedback."
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
