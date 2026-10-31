"""UX workflow contracts; agent turns and publication providers are mocked."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from orchestrator.prompts import build_system_prompt
from orchestrator.roles.loader import load_all_roles
from orchestrator.workflows.builtins import feature_workflow, bugfix_workflow, review_workflow
from orchestrator.workflows.merge_request import CreateMergeRequestStep
from orchestrator.workflows.steps import ReviewStep


class FeatureUXTests(unittest.IsolatedAsyncioTestCase):
    def context(self, text='APPROVED'):
        workflow = feature_workflow()
        agent = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(
            ok=True, full_text=text, error=None, files_changed=[], commands_run=[])))
        variables = dict(workflow.initial_variables, cwd='/repo', task_worktree='/repo',
                         task_branch='task', task_baseline='baseline', task_description='Improve form',
                         ux_assessment='Preserve labels', plan='Plan', implementation='Changes',
                         implementation_files=['form.tsx'], mr_status='prepared', mr_url='',
                         mr_branch='feature', mr_base='main', review_decision='approved',
                         review_commit='old-code-commit')
        return SimpleNamespace(variables=variables, get_agent_for_role=lambda role: agent,
                               acquire_thread=AsyncMock(return_value='thread'),
                               persist_step_thread=AsyncMock()), agent

    async def review(self, text, step=None):
        ctx, agent = self.context(text)
        with patch('orchestrator.workflows.merge_request._run',
                   AsyncMock(side_effect=['task', 'head', 'head'])):
            result = await (step or feature_workflow().get_step('ux-review')).execute(ctx)
        return result, ctx, agent

    def test_role_discovery_and_prompt(self):
        role = load_all_roles(Path(__file__).resolve().parents[1] / 'roles')['senior-ux-designer']
        self.assertEqual((role.backend, role.sandbox, role.approval_policy), ('codex', 'read-only', 'never'))
        self.assertIn('senior-ux-designer', feature_workflow().required_roles)
        prompt = build_system_prompt(role.name, role.system_prompt)
        for requirement in ('keyboard', 'permission', 'destructive', 'file and line', 'source alone'):
            self.assertIn(requirement, prompt)

    async def test_ux_output_is_independent_and_commit_bound(self):
        result, ctx, _ = await self.review('form.tsx:12 labels preserved.\nAPPROVED')
        self.assertEqual(result.next_step, 'review')
        self.assertEqual(result.output['ux_review_commit'], 'head')
        self.assertEqual(result.output['ux_review_decision'], 'approved')
        self.assertFalse(any(key.startswith('review_') for key in result.output))
        ctx.variables.update(result.output)
        self.assertEqual(ctx.variables['review_commit'], 'old-code-commit')
        self.assertEqual(ctx.variables['review_decision'], 'approved')

    async def test_missing_conflicting_and_embedded_decisions_fail_closed(self):
        for text in ('', 'Looks good', 'Not APPROVED', 'UNAPPROVED',
                     'APPROVED\nFurther comments', 'CHANGES_REQUESTED\nAPPROVED',
                     'APPROVED\nCHANGES_REQUESTED'):
            with self.subTest(text=text):
                result, _, _ = await self.review(text)
                self.assertEqual(result.output['ux_review_decision'], 'unclear')
                self.assertEqual(result.next_step, 'revise')

    async def test_ux_rejection_and_code_rejection_repeat_both_reviews(self):
        workflow = feature_workflow()
        for step_id in ('ux-review', 'review'):
            result, ctx, _ = await self.review('Required fix\nCHANGES_REQUESTED', workflow.get_step(step_id))
            self.assertEqual(result.next_step, 'revise')
            ctx.variables.update(result.output)
            feedback = workflow.get_step('revise').message_template.format(**ctx.variables)
            self.assertIn('Required fix', feedback)
            self.assertIn('Preserve labels', feedback)
            gate = await workflow.get_step('re-review-gate').execute(ctx)
            self.assertEqual(gate.next_step, 'ux-review')
            self.assertEqual(workflow.get_step('ux-review').on_approve, 'review')
            self.assertEqual(workflow.get_step('review').on_approve, 'done')

    async def test_both_reviews_use_same_selected_target_or_baseline(self):
        for base in ('release', ''):
            prompts = []
            for step_id in ('ux-review', 'review'):
                ctx, agent = self.context()
                ctx.variables['mr_base'] = base
                with patch('orchestrator.workflows.merge_request._run',
                           AsyncMock(side_effect=['task', 'head', 'head'])):
                    await feature_workflow().get_step(step_id).execute(ctx)
                prompts.append(agent.send_message.call_args.args[1])
            for prompt in prompts:
                self.assertIn(f'git diff origin/{base}...HEAD', prompt)
                self.assertIn('git diff baseline..HEAD', prompt)

    async def test_commit_changed_during_ux_review_fails(self):
        ctx, _ = self.context()
        with patch('orchestrator.workflows.merge_request._run',
                   AsyncMock(side_effect=['task', 'head', 'new-head'])):
            result = await feature_workflow().get_step('ux-review').execute(ctx)
        self.assertFalse(result.success)
        self.assertIn('changed during review', result.error)

    async def test_assessment_reaches_architect_and_implementer(self):
        workflow = feature_workflow()
        ids = [step.step_id for step in workflow.steps]
        self.assertLess(ids.index('prepare-mr'), ids.index('ux-assess'))
        self.assertLess(ids.index('ux-assess'), ids.index('plan'))
        ctx, agent = self.context()
        ctx.variables.pop('task_worktree')
        for step_id in ('plan', 'implement'):
            result = await workflow.get_step(step_id).execute(ctx)
            self.assertTrue(result.success)
            self.assertIn('Preserve labels', agent.send_message.call_args.args[1])
        self.assertIn('tradeoffs', workflow.get_step('plan').message_template)
        self.assertIn('backend-only', workflow.get_step('ux-assess').message_template)
        self.assertIn('Backend-only', workflow.get_step('ux-review').review_template)
        self.assertIn('outstanding visual/usability validation', workflow.get_step('done').message_template)
        self.assertEqual(workflow.max_iterations, 20)

    async def test_publication_blocks_missing_rejected_or_unclear_ux(self):
        for decision in (None, 'changes_requested', 'unclear'):
            ctx, _ = self.context()
            ctx.variables['ux_review_decision'] = decision
            with patch('orchestrator.workflows.merge_request._run', AsyncMock()) as run:
                result = await feature_workflow().get_step('create-mr').execute(ctx)
            self.assertFalse(result.success)
            self.assertIn('UX review approval', result.error)
            run.assert_not_awaited()

    async def test_publication_requires_both_current_commit_approvals(self):
        for code, ux, expected in (('head', None, 'UX'), ('head', 'old', 'UX'),
                                   ('old', 'head', 'Code'), ('head', 'head', None)):
            ctx, _ = self.context()
            ctx.variables.update(mr_repo='/repo', review_commit=code,
                                 ux_review_decision='approved', ux_review_commit=ux)
            # Stop at the next existing safety check when both approvals are current.
            run = AsyncMock(side_effect=['head', 'wrong-branch'])
            with patch('orchestrator.workflows.merge_request._run', run):
                result = await feature_workflow().get_step('create-mr').execute(ctx)
            self.assertFalse(result.success)
            self.assertIn(expected or 'changed branches', result.error)
            self.assertEqual(run.await_count, 1 if expected else 2)

    async def test_default_review_and_other_workflows_keep_existing_contract(self):
        step = ReviewStep('review', role='reviewer', review_template='{source}')
        result, _, _ = await self.review('APPROVED with notes', step)
        self.assertEqual(result.output, {'review_commit': 'head', 'review_decision': 'approved',
                                         'review_text': 'APPROVED with notes'})
        self.assertFalse(CreateMergeRequestStep('create').require_ux_approval)
        for workflow in (bugfix_workflow(), review_workflow()):
            self.assertNotIn('senior-ux-designer', workflow.required_roles)
