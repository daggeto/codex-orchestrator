"""Reviewer authority, revision routing, and saved checkpoint migrations."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from orchestrator.state.store import WorkflowRunRecord
from orchestrator.workflows.builtins import BUILTIN_WORKFLOWS, ui_small_workflow, backend_feature_workflow
from orchestrator.workflows.engine import continuation_checkpoint
from orchestrator.workflows.merge_request import CreateMergeRequestStep
from orchestrator.workflows.steps import AssignStep, ReviewStep, StepResult
from orchestrator.workflows.validation import FinalValidationStep


class ReviewerAuthorityTests(unittest.IsolatedAsyncioTestCase):
    def context(self, text):
        agent = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(
            ok=True, full_text=text, error=None, files_changed=[], commands_run=[])))
        return SimpleNamespace(
            variables={'cwd': '/repo', 'task_worktree': '/repo', 'task_branch': 'task',
                       'task_baseline': 'base', 'task_commit': 'head', 'task_description': 'Improve form',
                       'implementation': 'Changes', 'implementation_files': ['form.tsx'],
                       'plan': 'Preserve the API', 'ux_assessment': 'Preserve keyboard focus'},
            get_agent_for_role=lambda role: agent,
            acquire_thread=AsyncMock(return_value='thread'), persist_step_thread=AsyncMock(),
        ), agent

    def test_only_reviewer_owns_blocking_reviews_in_every_builtin(self):
        def visit(steps):
            for step in steps:
                if isinstance(step, ReviewStep) and not step.advisory:
                    self.assertEqual(step.role, 'reviewer')
                visit(getattr(step, 'sub_steps', []))
        for workflow in BUILTIN_WORKFLOWS.values():
            with self.subTest(workflow=workflow.name):
                visit(workflow.steps)
                for step in workflow.steps:
                    if isinstance(step, FinalValidationStep):
                        self.assertEqual(len(step.reviews), 1)
                        self.assertEqual(step.reviews[0].output_namespace, 'review')

    def test_publication_cannot_add_an_advisory_ux_approval_gate(self):
        with self.assertRaisesRegex(ValueError, 'approval belongs to the reviewer'):
            CreateMergeRequestStep('publish', require_ux_approval=True)

    def test_specialized_workflows_use_direct_reviewer_cycle(self):
        ui = ui_small_workflow()
        backend = backend_feature_workflow()
        for workflow in (ui, backend):
            self.assertIsNone(workflow.get_step('ux-review'))
            self.assertIsNone(workflow.get_step('review-gate'))
            self.assertEqual(workflow.get_step('revise').next_step, 'review')
            self.assertEqual(workflow.get_step('review').role, 'reviewer')
        self.assertEqual(ui.get_step('review').on_reject, 'revision-gate')
        self.assertEqual(backend.get_step('review').on_reject, 'revise')
        self.assertNotIn('architect', ui.required_roles)
        self.assertNotIn('senior-ux-designer', backend.required_roles)
        self.assertTrue(ui.get_step('ux-report').advisory)
        self.assertFalse(backend.get_step('create-mr').require_ux_approval)

    async def test_unclear_review_never_routes_to_implementer(self):
        ctx, agent = self.context('No remaining issues. No decision marker supplied.')
        step = ReviewStep('review', role='reviewer', review_template='{source}',
                          source_key='implementation', strict_decision=True, on_reject='revise')
        with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(return_value='head')):
            result = await step.execute(ctx)
        self.assertFalse(result.success)
        self.assertIsNone(result.next_step)
        self.assertEqual(result.output['review_decision'], 'unclear')
        prompt = agent.send_message.call_args.args[1]
        self.assertIn('Preserve keyboard focus', prompt)
        self.assertIn('Preserve the API', prompt)

    async def test_unchanged_rejected_commit_stops_with_implementer_report(self):
        ctx, _ = self.context('No changes needed')
        ctx.variables.update(review_decision='changes_requested', review_commit='head')
        step = AssignStep('revise', role='implementer', message_template='Fix review findings',
                          output_key='implementation', next_step='review')
        with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(return_value='head')), \
             patch('orchestrator.workflows.merge_request._run', AsyncMock(return_value='')):
            result = await step.execute(ctx)
        self.assertFalse(result.success)
        self.assertIsNone(result.next_step)
        self.assertIn('no new commit', result.error)
        self.assertEqual(result.output['implementation'], 'No changes needed')

    async def test_committed_revision_returns_to_reviewer(self):
        ctx, _ = self.context('Fixed the findings')
        ctx.variables.update(review_decision='changes_requested', review_commit='head')
        step = AssignStep('revise', role='implementer', message_template='Fix review findings',
                          output_key='implementation', next_step='review')
        with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(return_value='new-head')), \
             patch('orchestrator.workflows.merge_request._run', AsyncMock(return_value='')):
            result = await step.execute(ctx)
        self.assertTrue(result.success)
        self.assertEqual(result.next_step, 'review')
        self.assertEqual(result.output['task_commit'], 'new-head')

    def test_old_approval_policy_preserves_work_and_requires_fresh_review(self):
        workflow = ui_small_workflow()
        for step in ('review-gate', 'ux-review', 're-review-gate', 'repeat-reviews',
                     'review', 'validate', 'done', '__end__'):
            with self.subTest(step=step):
                variables = {'implementation': 'Committed work', 'task_worktree': '/repo',
                             'task_branch': 'task', 'task_baseline': 'base', 'create_mr': False,
                             'plan': 'Saved plan', 'ux_assessment': 'Saved UX criteria',
                             'review_decision': 'approved', 'review_commit': 'old-head',
                             'ux_review_decision': 'approved', 'completion_reviewed_sha': 'old-head',
                             'validation_attempt_0': {'commit': 'old-head'}}
                record = WorkflowRunRecord(run_id='run', workflow_name='ui-small', status='paused',
                    state={'checkpoint': {'next_step': step, 'variables': variables, 'iteration': 40,
                        'threads': {'implementer:codex:implement': 'implementation-thread',
                                    'reviewer:codex:review': 'obsolete-review-thread'}}})
                before = deepcopy(record)
                checkpoint = continuation_checkpoint(workflow, record)
                self.assertEqual(record, before)  # Availability checks must be read-only.
                self.assertEqual(checkpoint['next_step'], 'review')
                self.assertEqual(checkpoint['iteration'], 40)
                self.assertEqual(checkpoint['variables']['implementation'], 'Committed work')
                for key in ('task_worktree', 'task_branch', 'task_baseline', 'create_mr',
                            'plan', 'ux_assessment', 'validation_attempt_0'):
                    self.assertEqual(checkpoint['variables'][key], variables[key])
                for key in ('review_decision', 'review_commit', 'ux_review_decision', 'completion_reviewed_sha'):
                    self.assertNotIn(key, checkpoint['variables'])
                self.assertEqual(checkpoint['threads'], {'implementer:codex:implement': 'implementation-thread'})
                # Acceptance and execution both resolve the same checkpoint.
                record.state['checkpoint'] = checkpoint
                checkpoint['variables']['validation_resume_request'] = 'accepted-retry'
                self.assertEqual(continuation_checkpoint(workflow, record), checkpoint)

    def test_policy_migration_keeps_unfinished_implementation_step(self):
        workflow = ui_small_workflow()
        record = WorkflowRunRecord(run_id='run', workflow_name=workflow.name, status='failed',
            state={'checkpoint': {'next_step': 'implement', 'variables': {'plan': 'Saved plan'}, 'iteration': 3}})
        self.assertEqual(continuation_checkpoint(workflow, record)['next_step'], 'implement')

    def test_current_policy_keeps_saved_validation_and_review_threads(self):
        workflow = ui_small_workflow()
        checkpoint = {'next_step': 'validate', 'variables': {'review_decision': 'approved'},
                      'threads': {'reviewer:codex:review': 'current-thread'}, 'iteration': 12}
        record = WorkflowRunRecord(run_id='run', workflow_name=workflow.name,
            config={'review_policy': workflow.review_policy}, state={'checkpoint': checkpoint})
        self.assertEqual(continuation_checkpoint(workflow, record), checkpoint)

    def test_unknown_policy_and_missing_steps_still_reject_continuation(self):
        workflow = ui_small_workflow()
        record = WorkflowRunRecord(run_id='run', workflow_name=workflow.name,
            config={'review_policy': 'unknown'},
            state={'checkpoint': {'next_step': 'review', 'variables': {}, 'iteration': 40}})
        with self.assertRaisesRegex(ValueError, 'No checkpoint migration'):
            continuation_checkpoint(workflow, record)
        record.config.clear()
        record.state['checkpoint']['next_step'] = 'unknown-step'
        with self.assertRaisesRegex(ValueError, 'saved step is unavailable'):
            continuation_checkpoint(workflow, record)

    async def test_qa_repair_rejection_cycles_back_to_implementer_until_approved(self):
        workflow = backend_feature_workflow()
        validation = workflow.get_step('validate')
        ctx, _ = self.context('')
        ctx.variables.update(workflow.initial_variables)
        ctx.variables.update(review_decision='approved', review_commit='head')
        ctx.persist = AsyncMock()
        current = {'head': 'head', 'qa': 0, 'reviews': 0}
        stages = []

        async def git(root, *args):
            return current['head'] if args == ('git', 'rev-parse', 'HEAD') else ''

        async def execute(step):
            stages.append(step.step_id)
            if step.step_id == 'validation-prepare':
                return StepResult(True, output={'validation_environment_report': json.dumps({
                    'status': 'ready', 'reason': 'Task-local dependencies prepared',
                    'action': '', 'commands': [], 'qa_instructions': 'Use task-local tools',
                })})
            if step.step_id == 'validation-qa':
                current['qa'] += 1
                checks = [dict(category=category, command='check-' + category,
                               status='failed' if category == 'tests' and current['qa'] == 1 else 'passed',
                               reason='check evidence') for category in ('tests', 'typecheck', 'lint')]
                return StepResult(True, output={'validation_report': json.dumps({
                    'attempt_id': ctx.variables['validation_attempt_id'],
                    'commit': current['head'], 'checks': checks})})
            if step.step_id in {'validation-repair', 'validation-source-revision'}:
                current['head'] = 'qa-fix' if step.step_id == 'validation-repair' else 'review-fix'
                return StepResult(True, output={'task_commit': current['head'], 'implementation': 'Committed repair'})
            if step.step_id == 'review':
                current['reviews'] += 1
                return StepResult(True, output={'review_commit': current['head'],
                    'review_decision': 'changes_requested' if current['reviews'] == 1 else 'approved',
                    'review_text': 'Restore planned keyboard behavior'})
            self.fail('Unexpected stage: ' + step.step_id)

        ctx.execute_step = execute
        with patch('orchestrator.workflows.validation._run', git):
            result = await validation.execute(ctx)
        self.assertTrue(result.success, result.error)
        self.assertEqual(stages, ['validation-prepare', 'validation-qa', 'validation-repair', 'review',
                                  'validation-source-revision', 'review', 'validation-prepare', 'validation-qa'])
        self.assertEqual(result.output['validation_commit'], 'review-fix')
