"""Feature routing and advisory-report regressions; agents/providers are mocked."""
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
                         task_branch='task', task_baseline='baseline', task_commit='head',
                         task_description='Improve form', ux_assessment='Preserve labels',
                         plan='Technical plan', implementation='Changes',
                         implementation_files=['form.tsx'], mr_status='prepared', mr_url='',
                         mr_branch='feature', mr_base='main', review_decision='approved',
                         review_commit='head')
        return SimpleNamespace(variables=variables, get_agent_for_role=lambda role: agent,
                               acquire_thread=AsyncMock(return_value='thread'),
                               persist_step_thread=AsyncMock()), agent

    async def review(self, text, step_id='review', ctx=None):
        if ctx is None:
            ctx, agent = self.context(text)
        else:
            agent = ctx.get_agent_for_role('reviewer')
            agent.send_message.return_value.full_text = text
        with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(return_value='head')):
            result = await feature_workflow().get_step(step_id).execute(ctx)
        return result, ctx, agent

    def test_role_discovery_and_prompt(self):
        role = load_all_roles(Path(__file__).resolve().parents[1] / 'roles')['senior-ux-designer']
        self.assertEqual((role.backend, role.sandbox, role.approval_policy), ('codex', 'read-only', 'never'))
        self.assertIn('senior-ux-designer', feature_workflow().required_roles)
        prompt = build_system_prompt(role.name, role.system_prompt)
        for requirement in ('keyboard', 'permission', 'destructive', 'file and line', 'source alone'):
            self.assertIn(requirement, prompt)

    async def test_revision_returns_directly_to_code_review_with_both_plans(self):
        workflow = feature_workflow()
        result, ctx, agent = await self.review('Required fix\nCHANGES_REQUESTED')
        self.assertEqual(result.next_step, 'revise')
        ctx.variables.update(result.output)
        with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(return_value='head')), \
             patch('orchestrator.workflows.merge_request._run', AsyncMock(return_value='')):
            revised = await workflow.get_step('revise').execute(ctx)
        self.assertEqual(revised.next_step, 'review')
        prompt = agent.send_message.call_args.args[1]
        for text in ('Required fix', 'Preserve labels', 'Technical plan'):
            self.assertIn(text, prompt)
        approved, _, _ = await self.review('APPROVED', ctx=ctx)
        self.assertEqual(approved.next_step, 'final-ux-gate')

    async def test_unclear_decision_does_not_request_pointless_implementation(self):
        for text in ('', 'Looks good', 'Not APPROVED', 'UNAPPROVED',
                     'APPROVED\nFurther comments', 'CHANGES_REQUESTED\nAPPROVED'):
            with self.subTest(text=text):
                result, _, _ = await self.review(text)
                self.assertFalse(result.success)
                self.assertIsNone(result.next_step)

    async def test_material_consultation_returns_advice_then_reviewer_decides(self):
        for marker, step_id, key in (('CONSULT_UX', 'ux-consult', 'ux_consultation'),
                                     ('CONSULT_ARCHITECT', 'architect-consult', 'architecture_consultation')):
            with self.subTest(marker=marker):
                result, ctx, agent = await self.review('Resolve this material tradeoff\n' + marker)
                self.assertEqual(result.next_step, step_id)
                ctx.variables.update(result.output)
                agent.send_message.return_value.full_text = 'Scoped advice'
                with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(return_value='head')), \
                     patch('orchestrator.workflows.merge_request._run', AsyncMock(return_value='')):
                    advice = await feature_workflow().get_step(step_id).execute(ctx)
                self.assertEqual(advice.next_step, 'review')
                self.assertEqual(advice.output[key], 'Scoped advice')
                ctx.variables.update(advice.output)
                approved, _, agent = await self.review('APPROVED', ctx=ctx)
                self.assertTrue(approved.success)
                self.assertIn('Scoped advice', agent.send_message.call_args.args[1])
                repeated, _, _ = await self.review(marker, ctx=ctx)
                self.assertFalse(repeated.success)
                self.assertIn('already consulted', repeated.error)

    async def test_final_ux_violations_are_reported_without_revision_or_approval(self):
        text = 'P1 form.tsx:12: keyboard flow broken. Follow-up: restore focus.\nCHANGES_REQUESTED'
        result, ctx, _ = await self.review(text, 'ux-report')
        self.assertTrue(result.success)
        self.assertEqual(result.next_step, 'done')
        self.assertEqual(result.output['ux_report_text'], text)
        self.assertEqual(result.output['ux_report_status'], 'reported')
        self.assertNotIn('ux_review_decision', result.output)
        ctx.variables.update(result.output)
        with patch('orchestrator.workflows.feature.task_snapshot', AsyncMock(return_value='head')):
            summary = await feature_workflow().get_step('done').execute(ctx)
        self.assertTrue(summary.success)
        self.assertIn(text, summary.output['summary'])
        self.assertIn('were not automatically fixed', summary.output['summary'])
        self.assertFalse(hasattr(feature_workflow().get_step('done'), 'role'))

    async def test_final_ux_report_is_selective_and_follows_code_approval(self):
        workflow = feature_workflow()
        ctx, agent = self.context('UX criteria\nFINAL_UX_REVIEW_REQUIRED')
        ctx.variables.pop('task_worktree')
        assessment = await workflow.get_step('ux-assess').execute(ctx)
        self.assertTrue(assessment.output['ux_final_required'])
        ctx.variables.update(assessment.output)
        self.assertEqual((await workflow.get_step('final-ux-gate').execute(ctx)).next_step, 'ux-report')
        ctx.variables['ux_final_required'] = False
        self.assertEqual((await workflow.get_step('final-ux-gate').execute(ctx)).next_step, 'done')
        reviewer, _, _ = await self.review('FINAL_UX_REVIEW_REQUIRED\nAPPROVED')
        self.assertTrue(reviewer.output['ux_final_required'])
        self.assertEqual(reviewer.next_step, 'final-ux-gate')

    async def test_both_reviews_use_same_selected_target_or_baseline(self):
        for base in ('release', ''):
            for step_id in ('ux-report', 'review'):
                ctx, agent = self.context()
                ctx.variables['mr_base'] = base
                with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(return_value='head')):
                    await feature_workflow().get_step(step_id).execute(ctx)
                prompt = agent.send_message.call_args.args[1]
                self.assertIn(f'git diff origin/{base}...HEAD', prompt)
                self.assertIn('git diff baseline..HEAD', prompt)

    async def test_commit_changed_during_review_fails(self):
        ctx, _ = self.context()
        with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(side_effect=['head', 'new-head'])):
            result = await feature_workflow().get_step('review').execute(ctx)
        self.assertFalse(result.success)
        self.assertIn('changed during review', result.error)

    async def test_both_plans_reach_implementation_and_reviewer(self):
        workflow = feature_workflow()
        ctx, agent = self.context()
        ctx.variables.pop('task_worktree')
        for step_id in ('plan', 'implement', 'review'):
            result = await workflow.get_step(step_id).execute(ctx)
            self.assertTrue(result.success)
            self.assertIn('Preserve labels', agent.send_message.call_args.args[1])
        self.assertIn('tradeoffs', workflow.get_step('plan').message_template)
        self.assertIn('backend-only', workflow.get_step('ux-assess').message_template)
        self.assertEqual(workflow.max_iterations, 20)

    async def test_publication_requires_current_code_approval_not_ux_approval(self):
        step = feature_workflow().get_step('create-mr')
        self.assertFalse(step.require_ux_approval)
        for commit, expected in (('old', 'changed since approval'), ('head', 'changed branches')):
            ctx, _ = self.context()
            ctx.variables.update(mr_repo='/repo', review_commit=commit,
                                 ux_report_text='Unresolved UX violation', ux_report_status='reported')
            with patch('orchestrator.workflows.merge_request._run',
                       AsyncMock(side_effect=['head', 'wrong-branch'])):
                result = await step.execute(ctx)
            self.assertIn(expected, result.error)

    async def test_default_review_and_other_workflows_keep_existing_decision_contract(self):
        ctx, _ = self.context('APPROVED with notes')
        with patch('orchestrator.workflows.steps.task_snapshot', AsyncMock(return_value='head')):
            result = await ReviewStep('review', role='reviewer', review_template='{source}').execute(ctx)
        self.assertEqual(result.output['review_decision'], 'approved')
        self.assertFalse(CreateMergeRequestStep('create').require_ux_approval)
        for workflow in (bugfix_workflow(), review_workflow()):
            self.assertNotIn('senior-ux-designer', workflow.required_roles)
