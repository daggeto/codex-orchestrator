"""Incomplete turns must never become successful workflow handoffs."""
import unittest
from unittest.mock import AsyncMock

from orchestrator.agents.codex import CodexAgent
from orchestrator.protocol.events import StreamEvent
from orchestrator.roles.loader import RoleConfig


class AgentCompletionTests(unittest.IsolatedAsyncioTestCase):
    def agent(self, events):
        agent = CodexAgent('agent', RoleConfig(name='implementer'), port=9999)

        async def stream(*args):
            for event in events:
                yield event

        agent.stream_message = stream
        return agent

    async def test_partial_output_without_completion_is_failure(self):
        agent = self.agent([StreamEvent('item/agentMessage/delta', {'delta': 'Partial work'})])
        result = await agent.send_message('thread', 'Implement')
        self.assertFalse(result.ok)
        self.assertIn('without completion', result.error)

    async def test_provider_error_keeps_actionable_message(self):
        agent = self.agent([StreamEvent('error', {'error': {'message': 'Usage limit exceeded'}})])
        result = await agent.send_message('thread', 'Implement')
        self.assertFalse(result.ok)
        self.assertEqual(result.error, 'Usage limit exceeded')

    async def test_failed_completion_is_not_success(self):
        agent = self.agent([StreamEvent('turn/completed', {
            'turn': {'status': 'failed', 'error': {'message': 'Backend failed'}}})])
        result = await agent.send_message('thread', 'Implement')
        self.assertFalse(result.ok)
        self.assertEqual(result.error, 'Backend failed')

    async def test_only_final_answer_is_used_for_review_decision(self):
        agent = self.agent([
            StreamEvent('item/agentMessage/delta', {'delta': 'Previous review said CHANGES_REQUESTED\n'}),
            StreamEvent('item/completed', {'item': {'type': 'agentMessage',
                        'phase': 'final_answer', 'text': 'No blockers.\nAPPROVED'}}),
            StreamEvent('turn/completed', {'turn': {'status': 'completed'}}),
        ])
        result = await agent.send_message('thread', 'Review')
        self.assertTrue(result.ok)
        self.assertEqual(result.full_text, 'No blockers.\nAPPROVED')

    async def test_timeout_interrupts_backend_and_returns_failure(self):
        agent = CodexAgent('agent', RoleConfig(name='implementer', max_turn_seconds=0), port=9999)
        agent._request = AsyncMock(return_value={})
        agent.interrupt = AsyncMock()
        result = await agent.send_message('thread', 'Implement')
        self.assertFalse(result.ok)
        self.assertIn('timed out', result.error)
        agent.interrupt.assert_awaited_once_with('thread')
