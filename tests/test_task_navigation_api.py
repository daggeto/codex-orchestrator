"""Task listing and archive HTTP contracts without execution backends."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from orchestrator.agents.base import TurnResult
from orchestrator.api import tasks, workspaces
from orchestrator.state.store import StateStore
from orchestrator.tasks import TaskService
from orchestrator.recovery import WorkflowRecovery


class TaskNavigationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / 'state.db')
        self.addAsyncCleanup(self.store.close)
        await self.store.init()
        await self.store.write("INSERT INTO repositories VALUES ('repo','Repo','/repo','/repo/.git',1,'now','now')")
        await self.store.write("INSERT INTO workspaces VALUES ('main','repo','main','/repo','main','sha','active','now','now')")
        for n in range(25):
            await self.store.write("INSERT INTO tasks (task_id,workspace_id,title,prompt,kind,status,phase,settings_json,fingerprint,created_at,updated_at) VALUES (?,'main','Task','Prompt','agent',?,'Phase','{}','fp','now','now')", (f't{n:03}', 'waiting_input' if n == 0 else 'completed'))
        orch = SimpleNamespace(store=self.store)
        orch.tasks = TaskService(orch)
        orch.recovery = WorkflowRecovery(orch)
        app = web.Application(middlewares=[tasks.errors])
        app['orch'] = orch
        tasks.register(app)
        workspaces.register(app)
        self.client = TestClient(TestServer(app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()

    async def test_history_and_complete_open_listing(self):
        result = await (await self.client.get('/api/workspaces/main/tasks?before=')).json()
        self.assertEqual(25, len(result['tasks']))
        self.assertIsNone(result['next_cursor'])
        result = await (await self.client.get('/api/tasks?status=open')).json()
        self.assertEqual(['t000'], [t['task_id'] for t in result['tasks']])
        first = await (await self.client.get('/api/tasks?limit=20')).json()
        last = await (await self.client.get('/api/tasks?limit=20&before=' + first['next_cursor'])).json()
        self.assertEqual(5, len(last['tasks']))
        self.assertIsNone(last['next_cursor'])

    async def test_archive_validation_and_preview_filtering(self):
        for tid, body, status in [('t024', {'archived': 'yes'}, 400), ('missing', {'archived': True}, 404), ('t000', {'archived': True}, 409), ('t024', {'archived': True}, 200)]:
            response = await self.client.post('/api/tasks/' + tid + '/archive', json=body)
            self.assertEqual(status, response.status)
        archived = await (await self.client.get('/api/tasks?archived=only&limit=1')).json()
        self.assertEqual(['t024'], [t['task_id'] for t in archived['tasks']])
        preview = await (await self.client.get('/api/navigation')).json()
        # Recent twenty plus every unarchived task waiting for input.
        self.assertEqual([f't{n:03}' for n in range(23, 3, -1)] + ['t000'],
                         [task['task_id'] for task in preview['tasks']])
        self.assertNotIn('t024', [t['task_id'] for t in preview['tasks']])
        self.assertIn('t004', [t['task_id'] for t in preview['tasks']])
        response = await self.client.post('/api/tasks/t024/archive', json={'archived': False})
        self.assertIsNone((await response.json())['task']['archived_at'])

    async def test_failed_turn_outcome_reaches_all_task_surfaces(self):
        await self.store.task_status('t024', 'active', 'Turn completed with error')
        await self.store.record_turn('t024', TurnResult('thread', 'Partial', [], error='Turn completed with error'))
        for endpoint in ['/api/tasks/t024', '/api/tasks', '/api/navigation']:
            payload = await (await self.client.get(endpoint)).json()
            task = payload['task'] if 'task' in payload else next(t for t in payload['tasks'] if t['task_id'] == 't024')
            self.assertEqual('active', task['status'])
            self.assertIs(False, task['last_turn']['ok'])


class AttachmentHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from orchestrator.config import OrchestratorConfig
        from orchestrator.core import Orchestrator
        from orchestrator.api.server import create_app
        from aiohttp.test_utils import TestClient, TestServer
        from unittest.mock import AsyncMock
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.orch = Orchestrator(OrchestratorConfig(data_dir=Path(self.temp.name) / 'data', attachments_enabled=True))
        self.addAsyncCleanup(self.orch.store.close)
        await self.orch.store.init()
        self.addAsyncCleanup(self.orch.tasks.shutdown)
        await self.orch.store.write("INSERT INTO repositories VALUES ('repo','Repo','/repo','/repo/.git',1,'now','now')")
        await self.orch.store.write("INSERT INTO workspaces VALUES ('main','repo','main','/repo','main','sha','active','now','now')")
        self.orch.tasks.execute = AsyncMock()
        self.client = TestClient(TestServer(create_app(self.orch)))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()

    async def test_stream_acceptance_download_and_tombstone_contract(self):
        response = await self.client.post('/api/attachment-drafts', json={'workspace_id': 'main'})
        self.assertEqual(201, response.status)
        draft = await response.json()
        response = await self.client.put(f"/api/attachment-drafts/{draft['draft_id']}/files/upload", data=b'context', headers={'X-Filename': 'notes.txt'})
        self.assertEqual(201, response.status)
        self.assertNotIn('storage_key', await response.json())
        payload = {'workspace_id': 'main', 'prompt': '', 'kind': 'agent', 'isolation': 'never',
                   'draft_id': draft['draft_id'], 'attachment_ids': ['upload'], 'idempotency_key': 'stable'}
        first = await (await self.client.post('/api/tasks', json=payload)).json()
        second = await (await self.client.post('/api/tasks', json=payload)).json()
        self.assertEqual(first, second)
        url = f"/api/tasks/{first['task_id']}/attachments/upload/content"
        response = await self.client.get(url)
        self.assertEqual(b'context', await response.read())
        self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])
        self.assertEqual('no-store', response.headers['Cache-Control'])
        await self.orch.attachments.purge_workspace('main')
        self.assertEqual(410, (await self.client.get(url)).status)

    async def test_untrusted_origin_cannot_stream_uploads(self):
        response = await self.client.post('/api/attachment-drafts', json={'workspace_id': 'main'}, headers={'Origin': 'https://untrusted.example'})
        self.assertEqual(403, response.status)
