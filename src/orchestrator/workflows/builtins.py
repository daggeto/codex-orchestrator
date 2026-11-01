"""Built-in workflow templates."""

from __future__ import annotations

from orchestrator.workflows.engine import Workflow
from orchestrator.workflows.feature import FeatureReviewStep, FeatureSummaryStep, UXPlanStep
from orchestrator.workflows.merge_request import CreateMergeRequestStep, PrepareMergeRequestStep, PrepareWorktreeStep, VerifyMergeRequestStep
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
    """Plan UX and technology once; revise through code review; report final UX findings."""
    diff_scope = (
        "Publication status: {mr_status}\nSelected PR/MR: {mr_url}\n"
        "Publication source branch: {mr_branch}\nSelected target branch: {mr_base}\n"
        "Task checkout branch: {task_branch}\n\n"
        "When a target branch is selected, inspect the complete publication diff with "
        "git diff origin/{mr_base}...HEAD, including commits already on the branch. "
        "Use this selected target, not the provider default. If unavailable, report "
        "the review blocked instead of approving another diff. When no target is "
        "selected, publication is disabled; review git diff {task_baseline}..HEAD.\n\n"
    )
    return Workflow(
        name="feature",
        publication_policy="optional",
        initial_variables={"review_text": "Code review has not run yet.",
                           "ux_consultation": "No UX consultation requested.",
                           "architecture_consultation": "No architecture consultation requested.",
                           "ux_final_required": False},
        description="Feature: UX plan → technical plan → implement ⇄ code review → optional final UX report → optional publication",
        steps=[
            PrepareMergeRequestStep(step_id="prepare-mr", name="Prepare Task Branch"),
            UXPlanStep(
                step_id="ux-assess", name="UX Planning", role="senior-ux-designer",
                message_template=(
                    "Plan the user experience before technical planning.\n\n"
                    "Feature: {task_description}\nWorking directory: {cwd}\n\n"
                    "Trace existing flows and contracts. Define the smallest practical user flow, "
                    "interactions, loading/empty/error/success states, recovery, accessibility, "
                    "and numbered UX acceptance criteria with file and line evidence. "
                    "Own experience decisions; leave implementation architecture to the architect. "
                    "Separate source findings from assumptions and outstanding browser/user validation. "
                    "For backend-only work, give a brief applicability assessment without inventing UI work. "
                    "For a substantial UI/visual redesign, include the standalone marker "
                    "FINAL_UX_REVIEW_REQUIRED to request one final advisory report after code approval. "
                    "Routine or backend-only changes do not need that pass."
                ), cwd="{cwd}", output_key="ux_assessment",
            ),
            AssignStep(
                step_id="plan", name="Technical Planning", role="architect",
                message_template=(
                    "Design the technical implementation supporting the UX plan.\n\n"
                    "Feature: {task_description}\nUX plan and acceptance criteria:\n{ux_assessment}\n\n"
                    "Working directory: {cwd}\n"
                    "Specify files, data models, API/protocol contracts, ownership, persistence, "
                    "security, concurrency invariants, and technical acceptance criteria. "
                    "Trace all affected entry points, including continuation, restart, archive, "
                    "cleanup, and legacy paths where applicable. Verify external protocol contracts "
                    "from installed schemas or authoritative documentation before relying on them. "
                    "For async operations, define operation identity and monotonic terminal outcomes "
                    "across delayed success/failure, reloads, multiple tabs, and different operation IDs. "
                    "Resolve feasibility conflicts before implementation and explain tradeoffs or "
                    "UX recommendations you do not adopt. Do not independently redesign the experience."
                ), cwd="{cwd}", output_key="plan",
            ),
            AssignStep(
                step_id="implement", name="Implementation", role="implementer",
                message_template=(
                    "Implement both plans and the original request.\n\n"
                    "Technical plan:\n{plan}\n\nUX plan:\n{ux_assessment}\n\n"
                    "Original request: {task_description}\n\n"
                    "Map acceptance criteria to the implementation and explain deviations. "
                    "Follow repository validation restrictions. Finish and commit all task changes "
                    "before handing off; progress commentary or uncommitted code is not completion. "
                    "Stay on the selected branch. Do not push or open a PR/MR."
                ), cwd="{cwd}", output_key="implementation",
            ),
            FeatureReviewStep(
                step_id="review", name="Code Review", role="reviewer",
                review_template=(
                    "Review actual source for correctness, security, lifecycle safety, and compliance "
                    "with both plans. Plans may be wrong: verify the user's outcome too.\n\n"
                    "Original request: {task_description}\nTechnical plan:\n{plan}\n\n"
                    "UX plan and acceptance criteria:\n{ux_assessment}\n\n"
                    "Implementation output:\n{source}\nFiles changed: {implementation_files}\n\n"
                    "UX consultation:\n{ux_consultation}\nArchitecture consultation:\n{architecture_consultation}\n\n"
                ) + diff_scope + (
                    "Own protocol contracts, concurrency, persistence, and lifecycle checks. "
                    "Check every applicable UX and technical criterion, all supported blockers in "
                    "this snapshot, and the surrounding state model, not only the last reported symptom. "
                    "Keep cosmetic suggestions advisory. Source review is not browser acceptance. "
                    "Routine corrections go directly to the implementer. Consult UX only for a "
                    "material user-flow change, unresolved experience tradeoff, or design judgment. "
                    "Consult the architect only for material contract/data/ownership/concurrency/security "
                    "deviations requiring an architecture decision. Explain the exact question and end "
                    "with CONSULT_UX or CONSULT_ARCHITECT to request that consultation. "
                    "Use saved consultation advice before deciding; do not repeat a consultation for "
                    "the same commit. Neither specialist is a routine additional approval gate. "
                    "For substantial UI work, include FINAL_UX_REVIEW_REQUIRED on its own line. "
                    "Otherwise end with exactly one standalone final line: APPROVED or CHANGES_REQUESTED. "
                    "List all required fixes with file/line evidence, impact, and acceptance criteria "
                    "before that final line. Do not use decision markers elsewhere."
                ), source_key="implementation", strict_decision=True,
                on_approve="final-ux-gate", on_reject="revise",
            ),
            AssignStep(
                step_id="revise", name="Implementation Revision", role="implementer",
                message_template=(
                    "Address the code review findings, then return directly to code review.\n\n"
                    "Original request: {task_description}\nTechnical plan:\n{plan}\nUX plan:\n{ux_assessment}\n\n"
                    "Current code review feedback:\n{review_text}\n\n"
                    "UX consultation:\n{ux_consultation}\nArchitecture consultation:\n{architecture_consultation}\n\n"
                    "Resolve every finding and the underlying invariant, not just its reported symptom. "
                    "For async state, inspect delayed success/failure, reloads, multiple tabs, and "
                    "different operation IDs. Report each finding's resolution and any plan deviations. "
                    "Respect repository validation restrictions. Finish and commit the revision on "
                    "the selected branch. Do not push or open a PR/MR."
                ), cwd="{cwd}", output_key="implementation", next_step="review",
            ),
            AssignStep(
                step_id="ux-consult", name="UX Consultation", role="senior-ux-designer",
                message_template=(
                    "Answer the reviewer's specific experience question.\n"
                    "Request: {task_description}\nUX plan: {ux_assessment}\nTechnical plan: {plan}\n"
                    "Implementation: {implementation}\nReviewer question: {review_text}\n"
                    "Give a scoped decision, tradeoffs, and updated acceptance criteria. "
                    "Do not edit files or request a new workflow cycle. The reviewer consumes "
                    "your advice and owns the next implementation decision."
                ), cwd="{cwd}", output_key="ux_consultation", next_step="review", stable_snapshot=True,
            ),
            AssignStep(
                step_id="architect-consult", name="Architecture Consultation", role="architect",
                message_template=(
                    "Answer the reviewer's specific architecture question.\n"
                    "Request: {task_description}\nUX plan: {ux_assessment}\nTechnical plan: {plan}\n"
                    "Implementation: {implementation}\nReviewer question: {review_text}\n"
                    "Give a scoped technical decision, affected contracts/invariants, and acceptance "
                    "criteria. Do not edit files, redesign UX, or start another review cycle. "
                    "The reviewer consumes your advice and owns the next implementation decision."
                ), cwd="{cwd}", output_key="architecture_consultation", next_step="review", stable_snapshot=True,
            ),
            GateStep(
                step_id="final-ux-gate", name="Final UX Report Selection",
                condition_key="ux_final_required", on_true="ux-report", on_false="done",
            ),
            ReviewStep(
                step_id="ux-report", name="Final UX Report (Advisory)", role="senior-ux-designer",
                review_template=(
                    "Provide ONE final advisory UX report after code approval.\n\n"
                    "Feature: {task_description}\nUX plan: {ux_assessment}\nTechnical plan: {plan}\n"
                    "Implementation: {source}\nFiles changed: {implementation_files}\n"
                    "Code review: {review_text}\n\n"
                ) + diff_scope + (
                    "Inspect the agreed experience and cite file/line, affected task, user impact, "
                    "suggested fix, and acceptance criterion for remaining violations. "
                    "Record even substantial problems as prioritized unresolved follow-up work in "
                    "this report, then finish. Do not edit files, request changes, start another "
                    "cycle, or use approval/rejection decision markers. Findings are advisory and "
                    "do not block workflow completion or publication. Backend-only work needs a "
                    "brief applicability assessment. Distinguish source evidence from outstanding "
                    "visual, keyboard, responsive, and user validation. Do not run browser checks "
                    "or claim source alone establishes usability."
                ), source_key="implementation", output_namespace="ux_report", advisory=True,
                on_approve="done", on_reject="done",
            ),
            FeatureSummaryStep(step_id="done", name="Completion Report"),
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
            PrepareWorktreeStep(step_id="prepare-worktree", name="Prepare Task Worktree"),
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
        publication_policy="required",
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
