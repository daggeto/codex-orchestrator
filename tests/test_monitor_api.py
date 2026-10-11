import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
from orchestrator.api.server import create_app
from orchestrator.agents.base import AgentStatus, ThreadInfo
from orchestrator.agents.claude import ClaudeAgent
from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.registry import AgentRegistry
from orchestrator.config import OrchestratorConfig
from orchestrator.events.bus import EventBus
from orchestrator.protocol.events import StreamEvent
from orchestrator.roles.loader import RoleConfig
from orchestrator.state.store import StateStore, WorkItemRecord, WorkflowRunRecord


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / 'test.db')
        await self.store.init()
        self.agent = ClaudeAgent('agent-1', RoleConfig(name='implementer', backend='claude'))
        self.agent.status = AgentStatus.RUNNING
        self.orch = SimpleNamespace(store=self.store, registry=SimpleNamespace(
            agents={'agent-1': self.agent}, get_running_agents=lambda: [self.agent]),
            roles={}, workflows={}, bus=EventBus(), workspace=SimpleNamespace(list_worktrees=lambda: []))
        self.client = TestClient(TestServer(create_app(self.orch)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.store.close()
        self.tmp.cleanup()

    async def test_read_only_task_details_and_missing_record(self):
        rec = WorkItemRecord('task', 'Title', description='Details', input_data={'x': 1},
                             history=[{'action': 'assigned', 'agent': 'agent-1'}])
        await self.store.save_work_item(rec)
        response = await self.client.get('/api/work/task')
        self.assertEqual(response.status, 200)
        item = (await response.json())['item']
        self.assertEqual(item['description'], 'Details')
        self.assertEqual(item['input_data'], {'x': 1})
        self.assertEqual(item['history'], rec.history)
        summary = (await (await self.client.get('/api/work')).json())['items'][0]
        self.assertEqual(summary['created_at'], rec.created_at)
        self.assertEqual((await self.client.get('/api/work/missing')).status, 404)
        self.assertEqual((await self.store.get_work_item('task')).history, rec.history)

    async def test_runs_include_all_active_and_bounded_ordered_history(self):
        for i in range(55):
            await self.store.save_workflow_run(WorkflowRunRecord(
                f'run-{i:02}', 'flow', status='completed', state={'current_step': str(i)}))
        await self.store.save_workflow_run(WorkflowRunRecord('active', 'flow', status='running'))
        runs = await self.store.list_workflow_runs(recent_limit=2)
        self.assertEqual([r.run_id for r in runs], ['active', 'run-54', 'run-53'])
        response = await self.client.get('/api/workflows/runs')
        body = await response.json()
        self.assertEqual(len(body['runs']), 51)
        self.assertEqual(body['runs'][1]['state']['current_step'], '54')
        await self.store.close()
        self.store = StateStore(Path(self.tmp.name) / 'test.db')
        await self.store.init()
        self.assertEqual(len(await self.store.list_workflow_runs()), 51)

    async def test_agent_process_status_separate_from_activity(self):
        self.agent._register_thread(ThreadInfo('thread', 'agent-1', cwd='/workspace'))
        self.agent._begin_activity('thread', 'work')
        agent = (await (await self.client.get('/api/agents')).json())['agents'][0]
        self.assertEqual(agent['status'], 'running')
        self.assertEqual(agent['activity']['state'], 'working')
        self.assertEqual(agent['activity']['threads'][0]['workspace'], '/workspace')
        self.assertEqual(agent['name'], self.agent.name)

    async def test_stream_observer_failure_and_completion(self):
        def broken(*args):
            raise RuntimeError('observer unavailable')
        self.agent.set_observer(broken)
        async def send(message):
            self.assertEqual(self.agent.activity['state'], 'working')
            yield StreamEvent('item/agentMessage/delta', {'textDelta': 'hello'})
            yield StreamEvent('turn/completed')
        self.agent._sessions['thread'] = SimpleNamespace(send=send)
        result = await self.agent.send_message('thread', 'work')
        self.assertEqual(result.full_text, 'hello')
        self.assertEqual(self.agent.activity['state'], 'idle')
        self.assertEqual(self.agent.status, AgentStatus.RUNNING)

    async def test_unknown_events_and_nullable_error_payload(self):
        async def send(message):
            yield StreamEvent('future/event', {'turn': None})
            yield StreamEvent('error', {'turn': None})
            yield StreamEvent('turn/completed')
        self.agent._sessions['thread'] = SimpleNamespace(send=send)
        events = [event async for event in self.agent.stream_message('thread', 'work')]
        self.assertEqual(len(events), 3)
        self.assertEqual(self.agent.activity['state'], 'error')
        self.assertEqual(self.agent.activity['threads'], [])

    async def test_error_cancel_and_generator_close_cleanup(self):
        with self.assertRaises(ValueError):
            await self.agent.send_message('missing', 'work')
        self.assertEqual(self.agent.activity['state'], 'error')
        async def send(message):
            yield StreamEvent('item/started')
            await asyncio.Event().wait()
        self.agent._sessions['thread'] = SimpleNamespace(send=send)
        stream = self.agent.stream_message('thread', 'work')
        await anext(stream)
        await stream.aclose()
        self.assertEqual(self.agent.activity['state'], 'idle')
        task = asyncio.create_task(self.agent.send_message('thread', 'work'))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.agent.activity['threads'], [])

    async def test_codex_events_and_incomplete_turn_cleanup(self):
        agent = CodexAgent('codex', RoleConfig(name='implementer', max_turn_seconds=0), port=9001)
        async def request(*args, **kwargs):
            return {}
        agent._request = request
        observations = []
        agent.set_observer(lambda topic, payload: observations.append((topic, payload)))
        self.assertEqual([event async for event in agent.stream_message('thread', 'work')], [])
        self.assertEqual(agent.activity['state'], 'error')
        self.assertEqual(agent.activity['threads'], [])
        self.assertEqual([topic for topic, _ in observations], ['agent.activity', 'agent.activity'])

    async def test_codex_stream_preserves_and_forwards_events(self):
        agent = CodexAgent('codex', RoleConfig(name='implementer'), port=9001)
        observed = []
        agent.set_observer(lambda topic, payload: observed.append((topic, payload)))
        async def request(*args, **kwargs):
            queue = agent._event_queues['thread']
            queue.put_nowait(StreamEvent('item/agentMessage/delta', {'delta': 'hello'}))
            queue.put_nowait(StreamEvent('turn/completed', {'turn': {'status': 'failed', 'error': 'bad'}}))
            return {}
        agent._request = request
        events = [event async for event in agent.stream_message('thread', 'work')]
        self.assertEqual(len(events), 2)
        self.assertEqual(observed[1][1]['params'], {'delta': 'hello'})
        self.assertEqual(agent.activity['state'], 'error')
        self.assertEqual(agent.activity['threads'], [])

    async def test_bounded_forwarding_isolates_bus_failure(self):
        registry = AgentRegistry(OrchestratorConfig(), self.store, self.orch.bus)
        seen = []
        async def observe(event):
            seen.append(event)
        await self.orch.bus.subscribe('agent.event', observe)
        registry._observe('agent.event', {'agent_id': 'agent-1', 'method': 'unknown', 'params': {}})
        await registry._observation_task
        self.assertEqual(len(seen), 1)
        async def broken(*args, **kwargs):
            raise RuntimeError('bus unavailable')
        self.orch.bus.publish = broken
        for i in range(1000):
            registry._observe('agent.event', {'n': i})
        self.assertEqual(registry._observations.qsize(), 256)
        await registry._observation_task
        await registry.shutdown()
