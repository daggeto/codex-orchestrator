"""Regression coverage for durable hierarchy and task lifecycle; no backends launched."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from orchestrator.config import OrchestratorConfig
from orchestrator.core import Orchestrator
from orchestrator.state.store import AgentRecord, ThreadRecord, StateStore
from orchestrator.tasks import Conflict, TaskService
from orchestrator.roles.loader import RoleConfig


class OwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.db'
        self.store = StateStore(self.path)
        await self.store.init()
        await self.store.write("INSERT INTO repositories VALUES ('repo','Repo','/repo','/repo/.git',1,'now','now')")
        await self.store.write("INSERT INTO workspaces VALUES ('main','repo','main','/repo','main','sha','active','now','now')")
        self.orch = Mock()
        self.orch.store = self.store
        self.orch.config.default_model = 'default-model'
        self.orch.config.default_approval_policy = 'on-failure'
        self.orch.config.default_sandbox = 'workspace-write'
        self.orch.roles = {'writer': RoleConfig(name='writer')}
        self.service = TaskService(self.orch)
        self.service.execute = AsyncMock()

    async def asyncTearDown(self):
        await self.service.shutdown()
        await self.store.close()
        self.temp.cleanup()

    async def submit(self, key='key', prompt='Do work'):
        return await self.service.submit('main', prompt, 'agent', {'role': 'writer'}, idempotency_key=key)

    async def test_idempotency_preserves_one_task_and_configuration(self):
        first, second = await asyncio.gather(self.submit(), self.submit())
        self.assertEqual(first['task_id'], second['task_id'])
        self.assertEqual(1, len(await self.store.tasks('main')))
        self.assertIsNone(self.orch.roles['writer'].model)
        self.assertEqual('default-model', first['settings']['effective_role']['model'])
        with self.assertRaises(Conflict):
            await self.submit(prompt='Different work')

    async def test_restart_and_migration_preserve_history(self):
        task = await self.submit()
        await self.store.save_agent(AgentRecord('agent', 'writer', 'codex'))
        await self.store.write('UPDATE agents SET task_id=? WHERE agent_id=?', (task['task_id'], 'agent'))
        await self.store.save_thread(ThreadRecord('thread', 'agent'))
        await self.store.append_events([(task['task_id'], 'agent', 'thread', 'turn', None, 'user', '{"text":"Hello"}', 'now')])
        await self.store.delete_agent('agent')
        await self.store.close()
        await self.store.init()
        self.assertEqual(1, len(await self.store.events('agent')))
        self.assertIsNotNone(await self.store.get_thread('thread'))
        self.assertIsNotNone(await self.store.task(task['task_id']))
        self.assertEqual('2', (await self.store.one("SELECT value FROM meta WHERE key='schema_version'"))['value'])

    async def test_archive_blocks_new_submissions(self):
        await self.store.write("UPDATE workspaces SET status='archiving'")
        with self.assertRaises(Conflict):
            await self.submit()
        self.assertEqual([], await self.store.tasks('main'))

    async def test_input_can_only_be_answered_once(self):
        task = await self.submit()
        await self.store.create_input('input', task['task_id'], 'Continue?')
        self.assertTrue(await self.store.resolve_input('input', 'yes'))
        self.assertFalse(await self.store.resolve_input('input', 'no'))
        self.assertEqual([], await self.store.pending_inputs(task['task_id']))

    async def test_completed_session_rejects_messages(self):
        task = await self.submit()
        await asyncio.sleep(0)
        await self.service.shutdown()
        self.service.running.clear()
        await self.service.complete(task['task_id'])
        with self.assertRaises(Conflict):
            await self.service.send_message(task['task_id'], 'Continue')


class WorkspaceSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_worktree_is_reused_without_provisioning(self):
        from orchestrator.workspaces import WorkspaceService
        with tempfile.TemporaryDirectory() as directory:
            orch = Mock()
            orch.store.task = AsyncMock(return_value={'workspace_id': 'selected'})
            expected = {'workspace_id': 'selected', 'kind': 'worktree', 'status': 'active', 'path': directory}
            orch.store.workspace_record = AsyncMock(return_value=expected)
            orch.workspace.create_worktree = AsyncMock()
            self.assertEqual(expected, await WorkspaceService(orch).ensure_task_workspace('task', True))
            orch.workspace.create_worktree.assert_not_awaited()

    async def test_failed_provisioning_has_no_main_checkout_fallback(self):
        from orchestrator.workspaces import WorkspaceService
        with tempfile.TemporaryDirectory() as directory:
            orch = Mock()
            orch.store.task = AsyncMock(return_value={'workspace_id': 'main'})
            orch.store.workspace_record = AsyncMock(return_value={'kind': 'main', 'status': 'active', 'path': directory})
            orch.workspace.create_worktree = AsyncMock(side_effect=RuntimeError('cannot create checkout'))
            with self.assertRaisesRegex(RuntimeError, 'cannot create checkout'):
                await WorkspaceService(orch).ensure_task_workspace('task', True)
