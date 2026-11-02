"""Adapter regressions: durable turn outcomes and shared legacy participation."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from orchestrator.agents.base import ThreadInfo, TurnResult
from orchestrator.api.tasks import detail
from orchestrator.core import Orchestrator
from orchestrator.roles.loader import RoleConfig
from orchestrator.state import migrations
from orchestrator.state.store import AgentRecord, StateStore, ThreadRecord, WorkItemRecord
from orchestrator.tasks import TaskService
from orchestrator.recovery import WorkflowRecovery
from orchestrator.workspaces import WorkspaceService


class TaskAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.temp.name).resolve())
        self.store = StateStore(Path(self.root) / 'state.db')
        await self.store.init()
        await self.store.write("INSERT INTO repositories VALUES ('repo','Repo',?,'git',1,'now','now')", (self.root,))
        await self.store.write("INSERT INTO workspaces VALUES ('workspace','repo','worktree',?,'branch','sha','active','now','now')", (self.root,))
        self.role = RoleConfig(name='writer')
        self.orch = Orchestrator.__new__(Orchestrator)
        self.orch.store = self.store
        self.orch.recovery = WorkflowRecovery(self.orch)
        self.orch.config = SimpleNamespace(effective_project_dir=self.root)
        self.orch.workspaces = WorkspaceService(self.orch)
        self.orch.workspaces.register = AsyncMock()
        self.orch._audit_turn = AsyncMock()
        self.service = TaskService(self.orch)
        self.orch.tasks = self.service
        self.agent = SimpleNamespace(agent_id='agent', task_id=None, role=self.role, _threads={},
                                     activity={'state': 'idle', 'threads': []},
                                     start_thread=AsyncMock(return_value=ThreadInfo('thread', 'agent', self.root)),
                                     resume_thread=AsyncMock(return_value=ThreadInfo('thread', 'agent', self.root)))
        self.orch.registry = SimpleNamespace(materialize=AsyncMock(return_value=self.agent),
                                             get_agent=Mock(return_value=self.agent),
                                             start_agent=AsyncMock(), stop_agent=AsyncMock())
        await self.store.save_agent(AgentRecord('agent', 'writer', 'codex', config=asdict(self.role)))

    async def asyncTearDown(self):
        await self.orch.tasks.shutdown()
        await self.store.close()
        self.temp.cleanup()

    async def test_failed_turn_does_not_complete_a_work_item(self):
        settings = {'isolate': False, 'effective_role': asdict(self.role)}
        await self.store.write("INSERT INTO tasks (task_id,workspace_id,title,prompt,kind,settings_json,fingerprint,created_at,updated_at) VALUES ('task','workspace','Work','Work','agent',?,'','now','now')", (json.dumps(settings),))
        await self.store.write("UPDATE agents SET task_id='task' WHERE agent_id='agent'")
        await self.store.save_thread(ThreadRecord('thread', 'agent', self.root))
        self.agent.send_message = AsyncMock(return_value=TurnResult('thread', 'Partial response', [], files_changed=['partial.txt'], error='Turn failed'))
        await self.service.execute('task')
        task = await self.store.task('task')
        self.assertEqual('active', task['status'])
        self.assertFalse(task['last_turn']['ok'])
        await self.store.save_work_item(WorkItemRecord('item', 'Work', input_data={'cwd': self.root}))
        self.service.submit = AsyncMock(return_value=task)
        self.service.complete = AsyncMock()
        with patch('orchestrator.workflows.merge_request._run', AsyncMock(return_value=self.root)):
            self.assertEqual('Partial response', await self.orch.assign_work('item', 'writer'))
        item = await self.store.get_work_item('item')
        self.assertEqual('failed', item.status)
        self.assertEqual('Turn failed', item.output_data['error'])
        self.assertEqual(['partial.txt'], item.output_data['files'])
        self.service.complete.assert_not_awaited()
        self.assertEqual('active', (await self.store.task('task'))['status'])

    async def test_legacy_participation_and_continuation_survive_restart(self):
        ready = asyncio.Event()
        async def interrupted(thread_id, message):
            await self.store.append_events([(self.agent.task_id, 'agent', thread_id, 'turn', None, 'user', json.dumps({'text': message}), 'now')])
            ready.set()
            await asyncio.Future()
        self.agent.send_message = AsyncMock(side_effect=interrupted)
        operation = self.orch._legacy_operation(self.agent, 'First message', None)
        with patch('orchestrator.tasks._run', AsyncMock(return_value=self.root)):
            caller = asyncio.create_task(self.service.run_legacy(self.agent, 'First message', operation))
            await ready.wait()
            await self.service.shutdown()
            await asyncio.gather(caller, return_exceptions=True)
        task = (await self.store.tasks('workspace'))[0]
        tid = task['task_id']
        self.assertEqual('interrupted', task['status'])
        self.assertEqual('thread', task['settings']['legacy_thread_id'])
        self.assertFalse(task['settings']['isolate'])
        # Pre-revision legacy tasks also resume through the adapter rather than
        # entering ordinary execution with missing conversation settings.
        settings = task['settings']
        settings.pop('isolate')
        settings.pop('effective_role')
        await self.store.write('UPDATE tasks SET settings_json=? WHERE task_id=?', (json.dumps(settings), tid))
        await self.store.close()
        await self.store.init()
        self.orch.tasks = TaskService(self.orch)
        async def resumed(thread_id, message):
            await self.store.append_events([(self.agent.task_id, 'agent', thread_id, 'next', None, 'user', json.dumps({'text': message}), 'now')])
            return TurnResult(thread_id, 'Continued', [])
        self.agent.send_message = AsyncMock(side_effect=resumed)
        await self.orch.tasks.send_message(tid, 'Continue after restart')
        await self.orch.tasks.running[tid]
        latest = await self.store.task(tid)
        self.assertEqual('completed', latest['status'])
        self.assertTrue(latest['last_turn']['ok'])
        self.agent.resume_thread.assert_awaited_with('thread')
        self.assertEqual(['agent'], [agent['agent_id'] for agent in await self.store.task_agents(tid)])
        self.assertEqual([{'task_id': tid, 'thread_id': 'thread'}], await self.store.rows('SELECT * FROM task_threads WHERE task_id=?', (tid,)))
        self.assertIsNone((await self.store.one("SELECT * FROM agents WHERE agent_id='agent'"))['task_id'])
        response = await detail(SimpleNamespace(app={'orch': self.orch}, match_info={'task_id': tid}))
        self.assertEqual('agent', json.loads(response.text)['agents'][0]['agent_id'])
        await self.store.append_events([(None, 'agent', 'other', 'other', None, 'user', '{}', 'now')])
        events = await self.store.events('agent', task_id=tid)
        self.assertEqual(['First message', 'Continue after restart'], [event['payload']['text'] for event in events])


class ParticipationMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_three_legacy_links_are_backfilled(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / 'state.db')
            try:
                with patch.object(migrations, 'VERSION', 3):
                    await store.init()
                await store.write("INSERT INTO repositories VALUES ('repo','Repo','/repo','/repo/.git',1,'now','now')")
                await store.write("INSERT INTO workspaces VALUES ('workspace','repo','worktree','/repo/task','task','sha','active','now','now')")
                await store.write("INSERT INTO tasks (task_id,workspace_id,title,prompt,kind,settings_json,fingerprint,created_at,updated_at) VALUES ('task','workspace','Work','Work','agent',?,'','now','now')", (json.dumps({'legacy_agent_id': 'agent'}),))
                await store.save_agent(AgentRecord('agent', 'writer', 'codex'))
                await store.save_thread(ThreadRecord('thread', 'agent', '/repo/task'))
                await store.write("UPDATE threads SET task_id='task' WHERE thread_id='thread'")
                await store.close()
                await store.init()
                self.assertEqual('agent', (await store.task_agents('task'))[0]['agent_id'])
                self.assertEqual([{'task_id': 'task', 'thread_id': 'thread'}], await store.rows('SELECT * FROM task_threads'))
            finally:
                await store.close()


class AttachmentAdapterTests(unittest.TestCase):
    def test_codex_native_image_input_and_file_manifest(self):
        from orchestrator.agents.base import AgentInput, resource_safe_payload
        from orchestrator.agents.codex import CodexAgent
        resources = [dict(attachment_id='image', filename='diagram.png', path='/private/image',
                          media_type='image/png', byte_count=20, width=2)]
        blocks = CodexAgent.attachment_input(AgentInput('Review', 'message', resources))
        self.assertEqual({'type': 'localImage', 'path': '/private/image'}, blocks[1])
        self.assertIn('diagram.png', blocks[0]['text'])
        self.assertEqual({'type': 'image', 'payload': '[omitted]'},
                         resource_safe_payload({'type': 'image', 'source': {'data': 'secret'}}))

    def test_claude_attachment_transport_uses_stdin_without_write_roots(self):
        from orchestrator.agents.base import AgentInput
        from orchestrator.agents.claude import ClaudeSession
        session = ClaudeSession('thread', '/repo', RoleConfig(name='reader', backend='claude', sandbox='read-only'), 'claude')
        cmd = session._build_cmd(AgentInput('Review', attachments=[{'path': '/private/resource'}]))
        self.assertIn('--input-format', cmd)
        self.assertIn('stream-json', cmd)
        self.assertNotIn('--add-dir', cmd)
        self.assertNotIn('Review', cmd)
