"""Feature-specific planning, selective consultation, and completion reporting."""

from orchestrator.workflows.steps import AssignStep, ReviewStep, Step, StepResult, task_snapshot


def has_marker(text, marker):
    return marker in {line.strip() for line in text.splitlines()}


class UXPlanStep(AssignStep):
    async def execute(self, ctx):
        result = await super().execute(ctx)
        if result.success:
            result.output["ux_final_required"] = has_marker(
                result.output[self.output_key], "FINAL_UX_REVIEW_REQUIRED")
        return result


class FeatureReviewStep(ReviewStep):
    """Consult specialists for material deviations, never for routine corrections."""

    async def execute(self, ctx):
        result = await super().execute(ctx)
        if not result.success:
            return result
        text = result.output["review_text"]
        final = text.strip().splitlines()[-1] if text.strip() else ""
        routes = {"CONSULT_UX": "ux-consult", "CONSULT_ARCHITECT": "architect-consult"}
        if final in routes:
            # Decisions and consultation requests are mutually exclusive.
            if any(has_marker(text, marker) for marker in ("APPROVED", "CHANGES_REQUESTED")):
                return StepResult(success=False, error="Reviewer mixed a decision with a consultation request")
            key = routes[final] + "_commit"
            commit = result.output.get("review_commit")
            if key in ctx.variables and ctx.variables[key] == commit:
                return StepResult(success=False, error="Specialist already consulted for this commit; reviewer must decide using the saved advice")
            result.output.update({key: commit, "review_decision": "consultation_required"})
            result.next_step = routes[final]
        elif result.output["review_decision"] == "unclear":
            return StepResult(success=False, error="Reviewer must end with APPROVED, CHANGES_REQUESTED, CONSULT_UX, or CONSULT_ARCHITECT; no implementation revision was requested")
        if has_marker(text, "FINAL_UX_REVIEW_REQUIRED"):
            result.output["ux_final_required"] = True
        return result


class FeatureSummaryStep(Step):
    """Compile saved evidence without another architecture or review assignment."""

    async def execute(self, ctx):
        v = ctx.variables
        try:
            if v.get("review_decision") != "approved":
                raise ValueError("Code review approval is required before completion")
            head = await task_snapshot(ctx)
            if head and head != v.get("review_commit"):
                raise ValueError("Task commit changed since code review; completion report is stale")
        except (OSError, RuntimeError, ValueError) as exc:
            return StepResult(success=False, error=str(exc))
        sections = ["Code review approved the implementation.", v["implementation"],
                    "Code review:\n" + v["review_text"]]
        for key, label in (("ux_consultation", "UX consultation"),
                           ("architecture_consultation", "Architecture consultation"),
                           ("ux_report_text", "Final UX report (advisory; no revision cycle)")):
            if v.get(key):
                sections.append(label + ":\n" + v[key])
        if not v.get("ux_report_text"):
            sections.append("Final UX report was not required for this task.")
        if v.get("validation_summary"):
            sections.append("Final automated checks:\n" + v["validation_summary"])
        sections.append("Validation: source approval does not establish automated or browser acceptance. "
                        "Checks actually run and outstanding validation are recorded in the implementation and review reports. "
                        "UX findings above remain follow-up work; they were not automatically fixed.")
        return StepResult(success=True, output={"summary": "\n\n".join(sections)})
