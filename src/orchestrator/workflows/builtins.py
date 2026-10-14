"""Built-in workflow templates."""

from __future__ import annotations

from orchestrator.workflows.engine import Workflow
from orchestrator.workflows.merge_request import CreateMergeRequestStep, PrepareMergeRequestStep, VerifyMergeRequestStep
from orchestrator.workflows.pr_completion import (
    CompletionReviewStep, MonitorCompletionStep, PrepareCompletionStep,
    PublishCompletionStep, RepairCompletionStep,
)
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
    5. Push approved changes to an existing PR/MR or create a draft PR/MR
    6. Implementer monitors pending CI/deployment until green, reporting failures
    """
    return Workflow(
        name="feature",
        description="Feature: plan → implement → review → draft PR/MR → CI/deployment verification",
        steps=[
            PrepareMergeRequestStep(step_id="prepare-mr", name="Prepare Task Branch"),
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
                cwd="{cwd}",
                output_key="plan",
            ),
            AssignStep(
                step_id="implement",
                name="Implementation",
                role="implementer",
                message_template=(
                    "Implement the following plan. Write all the code changes needed.\n\n"
                    "Plan:\n{plan}\n\n"
                    "Original request: {task_description}\n\n"
                    "Stay on the current branch. Commit the task changes before finishing. "
                    "Do not push or open a PR/MR; the workflow handles publication after review."
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
                    "Inspect the complete PR/MR diff against its target branch (discover "
                    "the open request's target or the provider's default branch). Include "
                    "commits already on this branch, because all of it will be published.\n\n"
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
                    "Please address the feedback and update the code accordingly. "
                    "Stay on the current branch and commit the revision. Do not push or open a PR/MR."
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
                cwd="{cwd}",
            ),
            CreateMergeRequestStep(step_id="create-mr", name="Create Draft PR/MR"),
            VerifyMergeRequestStep(step_id="verify-mr", name="Verify PR/MR CI and Deployment"),
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


def pr_completion_workflow() -> Workflow:
    """Complete committed branch work, repairing CI failures after review."""
    return Workflow(
        name="pr-completion",
        description="Review → existing/new PR/MR → poll CI → repair/review/push until green",
        # Repair/review limits are enforced separately. Permit several steps per
        # repair while keeping the engine's independent loop safeguard.
        max_iterations=100,
        steps=[
            PrepareCompletionStep(step_id="prepare", name="Select PR/MR Branch"),
            CompletionReviewStep(step_id="review", name="Review Branch Changes"),
            PublishCompletionStep(step_id="publish", name="Push Approved PR/MR Changes"),
            MonitorCompletionStep(step_id="monitor", name="Monitor Latest Commit CI"),
            RepairCompletionStep(step_id="repair", name="Diagnose and Repair CI or Review Feedback"),
        ],
    )


BUILTIN_WORKFLOWS: dict[str, Workflow] = {
    "feature": feature_workflow(),
    "bugfix": bugfix_workflow(),
    "review": review_workflow(),
    "research": research_workflow(),
    "pr-completion": pr_completion_workflow(),
}
