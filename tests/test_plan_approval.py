"""Approval state transitions without real agents or repository mutations."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from orchestrator.workflows.builtins import feature_workflow, backend_feature_workflow, ui_small_workflow
from orchestrator.workflows.plan_approval import PlanApprovalStep


class PlanApprovalTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return SimpleNamespace(registry=SimpleNamespace(workflow_recovery=None), variables={'task_description': 'Use a design system', 'ux_assessment': 'UX criteria', 'plan': '# Plan\nUse components'})

    async def execute(self, ctx):
        step = feature_workflow().get_step('plan-approval')
        with patch('orchestrator.workflows.plan_approval.task_snapshot', AsyncMock(return_value='head')), \
             patch('orchestrator.recovery.checkout_evidence', AsyncMock(return_value={'head': 'head'})):
            result = await step.execute(ctx)
        ctx.variables.update(result.output)
        return result

    async def test_wait_and_exact_revision_approval(self):
        ctx = self.context()
        result = await self.execute(ctx)
        self.assertEqual(result.pause_reason, 'waiting_for_plan_approval')
        self.assertNotIn('approved_plan', ctx.variables)
        plan = ctx.variables['plan_approval']
        ctx.variables['_plan_action'] = {'action': 'approve'}
        result = await self.execute(ctx)
        self.assertEqual(result.next_step, 'implement')
        self.assertEqual(result.output['approved_plan'], plan['markdown'])
        self.assertEqual(plan['revision'], result.output['plan_approval']['approved_revision'])

    async def test_reroute_invalidates_approval_and_retains_feedback(self):
        ctx = self.context()
        await self.execute(ctx)
        ctx.variables['_plan_action'] = {'action': 'send_back', 'target': 'ux-assess', 'message': 'Use real components'}
        result = await self.execute(ctx)
        self.assertEqual(result.next_step, 'ux-assess')
        self.assertEqual(result.output['approved_plan'], '')
        self.assertIn('Use real components', result.output['planning_feedback'])
        result = await self.execute(ctx)
        self.assertEqual(result.pause_reason, 'waiting_for_plan_approval')
        self.assertEqual(ctx.variables['plan_approval']['revision'], 2)

    async def test_changed_checkout_blocks_approval(self):
        ctx = self.context()
        await self.execute(ctx)
        ctx.variables['plan_approval']['snapshot'] = 'older-head'
        ctx.variables['_plan_action'] = {'action': 'approve'}
        result = await self.execute(ctx)
        self.assertEqual(result.pause_reason, 'waiting_for_plan_approval')
        self.assertNotIn('approved_plan', result.output)
        self.assertIn('Checkout changed', result.output['plan_approval']['error'])

    def test_feature_only_and_generic_role_discovery(self):
        self.assertIsNone(backend_feature_workflow().get_step('plan-approval'))
        self.assertIsNone(ui_small_workflow().get_step('plan-approval'))
        workflow = feature_workflow()
        step = workflow.get_step('plan-approval')
        self.assertIsInstance(step, PlanApprovalStep)
        self.assertIn(step.author_role, workflow.required_roles)

    async def test_generic_workflow_has_no_ux_or_architect_assumptions(self):
        from orchestrator.workflows.engine import Workflow
        from orchestrator.workflows.steps import Step
        class Placeholder(Step):
            async def execute(self, ctx):
                raise AssertionError('Not executed')
        step = PlanApprovalStep('accept', author_role='researcher', sources=['proposal'],
                                targets={'research': 'Research'}, next_step='build')
        workflow = Workflow('custom', steps=[Placeholder('research'), step, Placeholder('build')])
        self.assertEqual(workflow.required_roles, ['researcher'])
        ctx = SimpleNamespace(variables={'proposal': '# Research proposal'})
        with patch('orchestrator.workflows.plan_approval.task_snapshot', AsyncMock(return_value='head')), \
             patch('orchestrator.recovery.checkout_evidence', AsyncMock(return_value={'head': 'head'})):
            result = await step.execute(ctx)
        self.assertEqual(result.output['plan_approval']['targets'], {'research': 'Research'})
        self.assertEqual(result.pause_reason, 'waiting_for_plan_approval')

    async def test_malformed_chat_does_not_replace_draft(self):
        ctx = self.context()
        await self.execute(ctx)
        original = ctx.variables['plan_approval']['markdown']
        agent = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(ok=True, full_text='not JSON')))
        ctx.get_agent_for_role = lambda role: agent
        ctx.acquire_thread = AsyncMock(return_value='thread')
        ctx.persist_step_thread = AsyncMock()
        ctx.variables['_plan_action'] = {'action': 'chat'}
        result = await self.execute(ctx)
        self.assertEqual(result.output['plan_approval']['markdown'], original)
        self.assertTrue(result.output['plan_approval']['error'])
        self.assertNotIn('approved_plan', result.output)
