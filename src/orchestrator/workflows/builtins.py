"""Built-in workflow templates."""

from __future__ import annotations

from orchestrator.workflows.engine import Workflow
from orchestrator.workflows.steps import (
    AssignStep,
    GateStep,
    HumanStep,
    ParallelStep,
    ReviewStep,
    SequenceStep,
)


def feature_workflow() -> Workflow:
    """
    Standard feature implementation workflow:
    1. Architect designs the approach
    2. Implementer writes the code
    3. Reviewer checks the work
    4. If rejected, loop back to implementer with feedback
    """
    return Workflow(
        name="feature",
        description="Full feature pipeline: architect → implement → review",
        steps=[
            AssignStep(
                step_id="plan",
                name="Architecture Planning",
                role="architect",
                message_template=(
                    "Design an implementation plan for the following feature request. "
                    "Include file changes needed, data model changes, and key decisions.\n\n"
                    "Feature: {task_description}\n\n"
                    "Working directory: {cwd}"
                ),
                output_key="plan",
            ),
            AssignStep(
                step_id="implement",
                name="Implementation",
                role="implementer",
                message_template=(
                    "Implement the following plan. Write all the code changes needed.\n\n"
                    "Plan:\n{plan}\n\n"
                    "Original request: {task_description}"
                ),
                cwd="{cwd}",
                output_key="implementation",
            ),
            ReviewStep(
                step_id="review",
                name="Code Review",
                role="reviewer",
                review_template=(
                    "Review the following implementation for correctness, security, and quality.\n\n"
                    "Original plan:\n{plan}\n\n"
                    "Implementation output:\n{source}\n\n"
                    "Files changed: {implementation_files}\n\n"
                    "End your review with either APPROVED or CHANGES_REQUESTED followed by "
                    "specific feedback."
                ),
                source_key="implementation",
                on_approve="done",
                on_reject="revise",
            ),
            AssignStep(
                step_id="revise",
                name="Revision",
                role="implementer",
                message_template=(
                    "The reviewer has requested changes to your implementation.\n\n"
                    "Review feedback:\n{review_text}\n\n"
                    "Please address the feedback and update the code accordingly."
                ),
                cwd="{cwd}",
                output_key="implementation",
            ),
            # After revision, go back to review
            GateStep(
                step_id="re-review-gate",
                name="Loop Back to Review",
                condition_key="review_decision",
                expected_value="changes_requested",
                on_true="review",
                on_false="done",
            ),
            AssignStep(
                step_id="done",
                name="Summary",
                role="architect",
                message_template=(
                    "The feature has been implemented and approved. "
                    "Summarize what was done:\n\n"
                    "Plan: {plan}\n"
                    "Review: {review_text}\n"
                    "Files changed: {implementation_files}"
                ),
                output_key="summary",
            ),
        ],
    )


def bugfix_workflow() -> Workflow:
    """
    Bug investigation and fix workflow:
    1. Investigator analyzes the bug
    2. Implementer fixes it
    3. QA verifies the fix
    """
    return Workflow(
        name="bugfix",
        description="Bug investigation → fix → verification",
        steps=[
            AssignStep(
                step_id="investigate",
                name="Bug Investigation",
                role="architect",
                message_template=(
                    "Investigate the following bug. Identify the root cause, affected files, "
                    "and propose a fix strategy.\n\n"
                    "Bug report: {task_description}\n"
                    "Working directory: {cwd}"
                ),
                output_key="investigation",
            ),
            AssignStep(
                step_id="fix",
                name="Bug Fix",
                role="implementer",
                message_template=(
                    "Fix the following bug based on this investigation:\n\n"
                    "Investigation:\n{investigation}\n\n"
                    "Original bug: {task_description}"
                ),
                cwd="{cwd}",
                output_key="fix",
            ),
            ReviewStep(
                step_id="verify",
                name="QA Verification",
                role="qa",
                review_template=(
                    "Verify that the following bug fix is correct and complete.\n\n"
                    "Original bug: {task_description}\n"
                    "Investigation: {investigation}\n"
                    "Fix output: {source}\n"
                    "Files changed: {fix_files}\n\n"
                    "Check for regressions and edge cases. "
                    "End with APPROVED or CHANGES_REQUESTED."
                ),
                source_key="fix",
                on_approve="__end__",
                on_reject="fix",
            ),
        ],
    )


def review_workflow() -> Workflow:
    """Simple code review workflow — send code to reviewer."""
    return Workflow(
        name="review",
        description="Send code for review",
        steps=[
            ReviewStep(
                step_id="review",
                name="Code Review",
                role="reviewer",
                review_template=(
                    "Review the following code or diff:\n\n{task_description}\n\n"
                    "End with APPROVED or CHANGES_REQUESTED."
                ),
                source_key="task_description",
            ),
        ],
    )


def research_workflow() -> Workflow:
    """Parallel research workflow — multiple agents investigate in parallel."""
    return Workflow(
        name="research",
        description="Parallel investigation by multiple agents",
        steps=[
            ParallelStep(
                step_id="parallel-research",
                name="Parallel Investigation",
                sub_steps=[
                    AssignStep(
                        step_id="research-arch",
                        role="architect",
                        message_template=(
                            "Research the following from an architecture perspective:\n\n"
                            "{task_description}"
                        ),
                        output_key="arch_analysis",
                    ),
                    AssignStep(
                        step_id="research-impl",
                        role="implementer",
                        message_template=(
                            "Research the following from an implementation perspective. "
                            "Look at the actual code and identify specifics:\n\n"
                            "{task_description}"
                        ),
                        output_key="impl_analysis",
                    ),
                ],
                merge_key="research_results",
            ),
            AssignStep(
                step_id="synthesize",
                name="Synthesis",
                role="architect",
                message_template=(
                    "Synthesize these parallel research findings into a coherent summary:\n\n"
                    "Architecture analysis: {arch_analysis}\n\n"
                    "Implementation analysis: {impl_analysis}\n\n"
                    "Provide a unified recommendation."
                ),
                output_key="synthesis",
            ),
        ],
    )


BUILTIN_WORKFLOWS: dict[str, Workflow] = {
    "feature": feature_workflow(),
    "bugfix": bugfix_workflow(),
    "review": review_workflow(),
    "research": research_workflow(),
}
