"""Late writes refresh recovery evidence without replaying stale decisions."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from orchestrator.recovery_actions import recover_step
from orchestrator.roles.loader import RoleConfig
from orchestrator.workflows.steps import AssignStep, StepResult


class RecoverySnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def recover(self, snapshots):
        role = RoleConfig(name='planner', sandbox='read-only')
        store = SimpleNamespace(
            one=AsyncMock(return_value={'attempts': 1, 'status': 'assessed',
                'created_at': datetime.now(timezone.utc).isoformat()}),
            write=AsyncMock())
        ctx = SimpleNamespace(variables={}, store=store,
            get_agent_for_role=lambda name: SimpleNamespace(role=role),
            execute_step=AsyncMock(return_value=StepResult(True)), persist=AsyncMock())
        service = SimpleNamespace(assess=AsyncMock(side_effect=[
            {'incident_id': 'original', 'decision': 'retry', 'summary': 'Old evidence'},
            {'incident_id': 'fresh', 'decision': 'retry', 'summary': 'Current evidence'}]))
        step = AssignStep('plan', 'planner', 'Plan')
        with patch('orchestrator.recovery_actions.task_snapshot', AsyncMock(side_effect=snapshots)):
            result = await recover_step(service, ctx, SimpleNamespace(steps=[]), step,
                StepResult(False, error='Turn timed out'))
        return result, service, ctx

    async def test_late_changes_are_reassessed_before_retry(self):
        result, service, ctx = await self.recover(['before', 'after', 'after', 'after', 'after'])
        self.assertTrue(result.success)
        self.assertEqual(service.assess.await_count, 2)
        evidence = service.assess.await_args_list[1].args[3]
        self.assertEqual(evidence['snapshot'], 'after')
        self.assertEqual(evidence['supersedes'], 'original')
        ctx.execute_step.assert_awaited_once()

    async def test_continued_changes_stop_without_another_assessment_or_retry(self):
        result, service, ctx = await self.recover(['before', 'after', 'changed-again'])
        self.assertFalse(result.success)
        self.assertEqual(service.assess.await_count, 2)
        ctx.execute_step.assert_not_awaited()
        saved = ctx.store.write.await_args_list[-1].args[1][1]
        self.assertIn('stop other writers', saved)
