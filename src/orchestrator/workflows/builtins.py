"""Built-in workflow templates."""

from __future__ import annotations

from orchestrator.workflows.engine import Workflow
from orchestrator.workflows.plan_approval import PlanApprovalStep
from orchestrator.workflows.feature import FeatureReviewStep, FeatureSummaryStep, UXPlanStep
from orchestrator.workflows.merge_request import CreateMergeRequestStep, PrepareMergeRequestStep, PrepareWorktreeStep, VerifyMergeRequestStep
from orchestrator.workflows.pr_completion import (
    CompletionReviewStep, MonitorCompletionStep, PrepareCompletionStep,
    PublishCompletionStep, RepairCompletionStep,
)
from orchestrator.workflows.validation import FinalValidationStep, DEFER_CHECKS
from orchestrator.workflows.steps import (
    AssignStep,
    GateStep,
    HumanStep,
    ParallelStep,
    ReviewStep,
    SequenceStep,
    StepResult,
)
from orchestrator.workflows.small_change import (
    SMALL_CHANGE_INSTRUCTIONS, SmallChangeAssignment, SmallChangeCheckpoint, SmallChangeSummary,
    MAX_SOURCE_REVISIONS, REVISION_REVIEW_INSTRUCTIONS, SmallChangeRevisionGate,
)


def _small_change_workflow() -> Workflow:
    """Implement and review with up to ten revisions before a user checkpoint."""
    review = (
        "Review the complete task diff with git diff {task_baseline}..HEAD in {cwd}.\n"
        "Request: {task_description}\nDeliverables: {source}\n"
        "Check correctness, security, and relevant usability together. Reuse existing UI "
        "patterns; block concrete defects, not cosmetic preferences or unrelated hardening. "
        "Respect repository validation policy; distinguish source review from checks run. "
        "Be concise and cite files and lines for required fixes. End with exactly one "
        "standalone final line: APPROVED or CHANGES_REQUESTED. Do not use these markers elsewhere."
    ) + REVISION_REVIEW_INSTRUCTIONS
    return Workflow(
        name="small-change",
        description="Small local change → combined review → up to 10 revisions; unresolved findings include the reviewer's explanation",
        max_iterations=MAX_SOURCE_REVISIONS * 3 + 10,
        initial_variables={"source_revision_count": 0},
        role_overrides={
            "implementer": {"config_overrides": {"model_reasoning_effort": '"medium"'}},
            "reviewer": {"config_overrides": {"model_reasoning_effort": '"medium"'}},
        },
        steps=[
            PrepareWorktreeStep(step_id="prepare-worktree", name="Prepare Task Worktree"),
            SmallChangeAssignment(
                step_id="implement", name="Small Change", role="implementer",
                message_template="Request: {task_description}\nCheckout: {cwd}\n\n" + SMALL_CHANGE_INSTRUCTIONS,
                output_key="implementation", thread_key="small-change-implementation",
            ),
            ReviewStep(step_id="review", name="Combined Review", role="reviewer",
                       review_template=review, source_key="implementation", strict_decision=True,
                       thread_key="small-change-review",
                       on_approve="done", on_reject="revision-gate"),
            SmallChangeRevisionGate(step_id="revision-gate", name="Review Revision Limit"),
            SmallChangeAssignment(
                step_id="revise", name="Revise Review Findings", role="implementer",
                message_template="Request: {task_description}\nRequired fixes: {review_text}\n\n" + SMALL_CHANGE_INSTRUCTIONS,
                output_key="implementation", thread_key="small-change-implementation",
            ),
            ReviewStep(step_id="verify", name="Verify Revision", role="reviewer",
                       review_template=review, source_key="implementation", strict_decision=True,
                       thread_key="small-change-review",
                       on_approve="done", on_reject="revision-gate"),
            SmallChangeCheckpoint(step_id="checkpoint", name="User Decision Required"),
            SmallChangeSummary(step_id="done", name="Summary"),
        ],
    )


def _feature_workflow() -> Workflow:
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
        description="Feature: UX plan → technical plan → developer approval → implement ⇄ code review → optional final UX report → optional publication",
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
                    "Produce a consolidated Markdown plan including UX decisions, outcome, scope, numbered acceptance criteria, "
                    "assumptions and explicit deviations from the original request. Do not edit repository files. "
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


def _bugfix_workflow() -> Workflow:
    """
    Bug investigation and fix workflow:
    1. Investigator analyzes the bug
    2. Implementer fixes it
    3. Reviewer approves against the investigation; QA performs final checks
    """
    return Workflow(
        name="bugfix",
        description="Bug investigation → implementer fix ⇄ reviewer approval",
        initial_variables={"review_text": "No review findings yet."},
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
                    "Original bug: {task_description}\n"
                    "Reviewer findings: {review_text}\n"
                    "Address all findings, preserve the investigation strategy, and commit the fix."
                ),
                cwd="{cwd}",
                output_key="fix",
            ),
            ReviewStep(
                step_id="verify",
                name="Reviewer Approval",
                role="reviewer",
                review_template=(
                    "Verify that the following bug fix is correct and complete.\n\n"
                    "Original bug: {task_description}\n"
                    "Investigation and acceptance criteria: {investigation}\n"
                    "Fix output: {source}\n"
                    "Files changed: {fix_files}\n\n"
                    "Verify the fix matches the investigation strategy and all applicable UX and architecture criteria. Check regressions and edge cases. "
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
        review_policy="reviewer-v1",
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
                source_key="task_description", strict_decision=True,
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
        review_policy="reviewer-v1",
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


def _final_checks(workflow):
    workflow.review_policy = "reviewer-v1"
    workflow.initial_variables.setdefault('ux_assessment', 'No separate UX plan supplied; use applicable request criteria.')
    workflow.initial_variables.setdefault('plan', 'No separate architecture plan supplied; preserve existing contracts.')
    workflow.initial_variables.setdefault('review_text', 'No reviewer findings yet.')
    # Alternative review/verify steps share one decision and one approved commit.
    reviews = list({s.output_namespace: s for s in reversed(workflow.steps)
                    if isinstance(s, ReviewStep) and s.role == 'reviewer' and not s.advisory}.values())
    for step in workflow.steps:
        if isinstance(step, AssignStep):
            step.message_template += DEFER_CHECKS
            if step.step_id == 'done':
                step.message_template += '\nFinal validation evidence: {validation_summary}'
        if isinstance(step, ReviewStep):
            step.strict_decision = True
            step.review_template += DEFER_CHECKS
            if not step.advisory and not isinstance(step, FeatureReviewStep):
                step.review_template += '\nEnd with exactly one standalone final line: APPROVED or CHANGES_REQUESTED. Do not use these markers elsewhere.'
    final = 'done' if workflow.name != 'bugfix' else '__end__'
    for step in workflow.steps:
        if isinstance(step, GateStep) and step.step_id == 'final-ux-gate':
            step.on_false = 'validate'
        if isinstance(step, ReviewStep) and step.on_approve == final:
            step.on_approve = 'validate'
    index = next((i for i, s in enumerate(workflow.steps) if s.step_id == 'done'), len(workflow.steps))
    consultations = {s.step_id: s for s in workflow.steps if s.step_id in {'ux-consult', 'architect-consult'}}
    workflow.steps.insert(index, FinalValidationStep(step_id='validate', name='Final Automated Checks',
                                                     reviews=reviews, consultations=consultations))
    workflow.max_iterations = max(workflow.max_iterations, 20)
    workflow.description += ' → final checks (up to three repairs per accepted cycle)'
    return workflow


def small_change_workflow():
    return _final_checks(_small_change_workflow())


def feature_workflow():
    workflow = _feature_workflow()
    workflow.initial_variables['_requires_plan_approval'] = True
    index = next(i for i, step in enumerate(workflow.steps) if step.step_id == 'implement')
    workflow.steps.insert(index, PlanApprovalStep(step_id='plan-approval', name='Developer Plan Approval',
        author_role='architect', sources=['ux_assessment', 'plan'],
        targets={'ux-assess': 'UX planning', 'plan': 'Architect'}, next_step='implement'))
    return _final_checks(workflow)


def bugfix_workflow():
    return _final_checks(_bugfix_workflow())


def ui_small_workflow():
    workflow = _feature_workflow()
    workflow.name = 'ui-small'
    workflow.description = 'Small UI change: UX brief → implement ⇄ reviewer approval → optional advisory UX report → final checks; local only'
    workflow.max_iterations = MAX_SOURCE_REVISIONS * 4 + 20
    workflow.publication_policy = 'none'
    workflow.steps = [s for s in workflow.steps if s.step_id not in {'plan', 'create-mr', 'verify-mr', 'architect-consult'}]
    workflow.steps[0] = PrepareWorktreeStep(step_id='prepare-worktree')
    workflow.initial_variables.update(plan='Use the brief UX assessment and existing patterns.',
                                     mr_status='disabled', mr_url='', mr_branch='', mr_base='',
                                     source_revision_count=0)
    for i, step in enumerate(workflow.steps):
        if step.step_id == 'ux-assess':
            step.message_template += ('\nKeep this brief: existing pattern, proposed change, and acceptance criteria. '
                                      'No broad redesign. Keep the required proposal within small-task rules. '
                                      'Put optional features or work exceeding those rules in separate follow-up suggestions. '
                                      'Do not turn editable labels, migrations, or existing Git renames into prerequisites.')
        if step.step_id in {'implement', 'revise'}:
            feedback = 'Reviewer findings: {review_text}\n' if step.step_id == 'revise' else ''
            workflow.steps[i] = SmallChangeAssignment(
                step_id=step.step_id, role='implementer', output_key='implementation',
                message_template='Request: {task_description}\nUX brief: {ux_assessment}\n' + feedback + SMALL_CHANGE_INSTRUCTIONS,
                thread_key='ui-small-implementation',
                next_step='review' if step.step_id == 'revise' else None)
        if step.step_id == 'review':
            step.on_reject = 'revision-gate'
            step.review_template += REVISION_REVIEW_INSTRUCTIONS
            step.thread_key = 'ui-small-review'
            step.consultation_routes = {'CONSULT_UX': 'ux-consult'}
            step.review_template = step.review_template.replace('CONSULT_UX or CONSULT_ARCHITECT', 'CONSULT_UX')
            step.review_template += '\nNo architect is assigned to this small task. Escalate architecture changes as scope findings; do not request architecture consultation.'
    workflow.steps.insert(next(i for i, s in enumerate(workflow.steps) if s.step_id == 'revise'),
                          SmallChangeRevisionGate(step_id='revision-gate'))
    workflow.steps.insert(next(i for i, s in enumerate(workflow.steps) if s.step_id == 'done'),
                          SmallChangeCheckpoint(step_id='checkpoint'))
    workflow.role_overrides = {r: {'config_overrides': {'model_reasoning_effort': '"medium"'}}
                               for r in ['senior-ux-designer', 'implementer', 'reviewer', 'qa']}
    return _final_checks(workflow)


def backend_feature_workflow():
    workflow = _feature_workflow()
    workflow.name = 'backend-feature'
    workflow.description = 'Backend feature: architecture plan → implement ⇄ reviewer approval → final checks → optional publication'
    workflow.steps = [s for s in workflow.steps if s.step_id not in {'ux-assess', 'ux-consult', 'ux-report', 'final-ux-gate'}]
    workflow.initial_variables['ux_assessment'] = 'Backend task: no dedicated UX plan. Disclose any necessary UI changes and pending UX validation.'
    for step in workflow.steps:
        if isinstance(step, AssignStep):
            step.message_template += '\nIdentify UI changes and their rationale. Dedicated UX assessment was not performed.'
        if step.step_id == 'review':
            step.on_approve = 'done'
            step.consultation_routes = {'CONSULT_ARCHITECT': 'architect-consult'}
            step.review_template = step.review_template.replace('CONSULT_UX or CONSULT_ARCHITECT', 'CONSULT_ARCHITECT')
            step.review_template += '\nNo UX specialist is assigned. Review applicable usability criteria yourself and disclose pending UX validation. Do not request UX consultation or a final UX report.'
    return _final_checks(workflow)


BUILTIN_WORKFLOWS: dict[str, Workflow] = {
    "small-change": small_change_workflow(),
    "ui-small": ui_small_workflow(),
    "backend-feature": backend_feature_workflow(),
    "feature": feature_workflow(),
    "bugfix": bugfix_workflow(),
    "review": review_workflow(),
    "research": research_workflow(),
    "pr-completion": pr_completion_workflow(),
}
