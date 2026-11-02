"""Incomplete turns must never become successful workflow handoffs."""
import asyncio
import unittest
from unittest.mock import AsyncMock

from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.claude import ClaudeAgent
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
        agent = CodexAgent('agent', RoleConfig(name='implementer', max_turn_seconds=0.001), port=9999)
        agent._request = AsyncMock(return_value={})
        interrupted = asyncio.Event()

        async def interrupt(thread_id):
            interrupted.set()

        agent.interrupt = AsyncMock(side_effect=interrupt)
        task = asyncio.create_task(agent.send_message('thread', 'Implement'))
        self.addAsyncCleanup(task.cancel)
        await interrupted.wait()
        self.assertFalse(task.done(), 'Recovery must wait for the turn to stop')
        agent._event_queues['thread'].put_nowait(StreamEvent('turn/completed', {
            'turn': {'status': 'interrupted'}}))
        result = await task
        self.assertFalse(result.ok)
        self.assertIn('timed out', result.error)
        agent.interrupt.assert_awaited_once_with('thread')

    async def test_failed_timeout_interrupt_stops_backend_before_returning(self):
        agent = CodexAgent('agent', RoleConfig(name='implementer', max_turn_seconds=0.001), port=9999)
        agent._request = AsyncMock(return_value={})
        agent.interrupt = AsyncMock(side_effect=RuntimeError('No acknowledgement'))
        agent.stop = AsyncMock()
        result = await agent.send_message('thread', 'Implement')
        self.assertFalse(result.ok)
        agent.stop.assert_awaited_once()

    async def test_interrupt_identifies_the_active_turn(self):
        agent = CodexAgent('agent', RoleConfig(name='implementer'), port=9999)
        agent._active_turn_ids['thread'] = 'active-turn'
        agent._request = AsyncMock()
        await agent.interrupt('thread')
        agent._request.assert_awaited_once_with('turn/interrupt', {
            'threadId': 'thread', 'turnId': 'active-turn'})

    async def test_zero_limit_waits_for_codex_completion(self):
        agent = CodexAgent('agent', RoleConfig(name='planner', max_turn_seconds=0), port=9999)
        agent.interrupt = AsyncMock()

        async def request(*args, **kwargs):
            await asyncio.sleep(0)
            agent._event_queues['thread'].put_nowait(StreamEvent(
                'turn/completed', {'turn': {'status': 'completed'}}))
            return {}

        agent._request = request
        result = await agent.send_message('thread', 'Plan')
        self.assertTrue(result.ok)
        agent.interrupt.assert_not_awaited()

    async def test_default_limit_waits_for_claude_completion(self):
        role = RoleConfig(name='planner', backend='claude')
        self.assertEqual(role.max_turn_seconds, 0)
        agent = ClaudeAgent('agent', role)

        class Session:
            interrupt = AsyncMock()

            async def send(self, message):
                await asyncio.sleep(0)
                yield StreamEvent('turn/completed', {'turn': {'status': 'completed'}})

        session = Session()
        agent._sessions['thread'] = session
        result = await agent.send_message('thread', 'Plan')
        self.assertTrue(result.ok)
        session.interrupt.assert_not_awaited()
