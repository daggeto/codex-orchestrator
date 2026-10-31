"""Regression cases for review findings. Backends and provider calls are mocked."""
import asyncio
import json
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from orchestrator.agents.base import AgentStatus
from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.registry import AgentRegistry
from orchestrator.api.filesystem import directories
from orchestrator.archives import ArchiveService
from orchestrator.core import Orchestrator
from orchestrator.processes import owner_alive, stop_process_group
from orchestrator.ports import PortReservations
from orchestrator.roles.loader import RoleConfig
from orchestrator.state import migrations
from orchestrator.state.store import AgentRecord, StateStore
from orchestrator.tasks import Conflict, TaskService
from orchestrator.workspaces import WorkspaceService
from orchestrator.workflows.merge_request import PrepareMergeRequestStep


class LifecycleRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_allocates_a_port_owned_by_the_restarted_agent(self):
        registry = AgentRegistry(SimpleNamespace(base_port=15000, max_agents=10),
                                 SimpleNamespace(write=AsyncMock(), update_agent_status=AsyncMock()),
                                 SimpleNamespace(publish=AsyncMock()))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        registry._port_reservations = PortReservations(directory.name)
        self.addCleanup(lambda: [registry._release_port(port, owner) for port, owner in list(registry._allocated_ports.items())])
        first = CodexAgent('a', RoleConfig(name='writer'), registry._allocate_port('a'))
        registry._agents['a'] = first
        registry._release_port(first.port, 'a')
        second_port = registry._allocate_port('b')
        first.start = AsyncMock()
        await registry.start_agent('a')
        self.assertNotEqual(second_port, first.port)
        self.assertEqual('a', registry._allocated_ports[first.port])
        self.assertEqual(f'ws://{first.host}:{first.port}', first.ws_url)
        registry._release_port(second_port, 'a')
        self.assertEqual('b', registry._allocated_ports[second_port])

    async def test_codex_stop_cancels_and_awaits_server_input_handlers(self):
        agent = CodexAgent('agent', RoleConfig(name='writer'), 15000)
        waiting = asyncio.Event()
        cleaned = asyncio.Event()
        async def handler():
            try:
                waiting.set()
                await asyncio.Future()
            finally:
                cleaned.set()
        job = asyncio.create_task(handler())
        agent._server_requests.add(job)
        await waiting.wait()
        await agent.stop()
        self.assertTrue(job.cancelled())
        self.assertTrue(cleaned.is_set())
        self.assertEqual(AgentStatus.STOPPED, agent.status)

    async def test_version_two_database_upgrades_without_losing_saved_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / 'state.db')
            try:
                with patch.object(migrations, 'VERSION', 2):
                    await store.init()
                await store.write("INSERT INTO agents (agent_id,role,backend,created_at,updated_at) VALUES ('saved','writer','codex','now','now')")
                await store.close()
                await store.init()
                saved = await store.one("SELECT * FROM agents WHERE agent_id='saved'")
                self.assertEqual('writer', saved['role'])
                self.assertIsNone(saved['owner_pid'])
                self.assertEqual(str(migrations.VERSION),
                                 (await store.one("SELECT value FROM meta WHERE key='schema_version'"))['value'])
                columns = await store.rows('PRAGMA table_info(workspace_leases)')
                self.assertIn('owner_started', {column['name'] for column in columns})
            finally:
                await store.close()

    async def test_directory_picker_includes_permitted_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'child').mkdir()
            (root / '.hidden').mkdir()
            (root / 'file').write_text('content')
            orch = SimpleNamespace(config=SimpleNamespace(browse_roots=[str(root)]))
            request = SimpleNamespace(app={'orch': orch}, query={'path': str(root)})
            response = await directories(request)
            self.assertEqual([{'name': 'child', 'path': str(root / 'child')}],
                             json.loads(response.text)['directories'])

    async def test_group_shutdown_signals_children_after_parent_exit(self):
        proc = SimpleNamespace(pid=12345, returncode=0, wait=AsyncMock())
        signals = []
        def killpg(pid, sig):
            signals.append(sig)
            if sig == 0:
                raise ProcessLookupError
        with patch('orchestrator.processes.os.killpg', side_effect=killpg):
            await stop_process_group(proc)
        self.assertIn(signal.SIGTERM, signals)
        proc.wait.assert_awaited()

    async def test_legacy_send_delegates_to_the_shared_task_lifecycle(self):
        orch = Orchestrator.__new__(Orchestrator)
        agent = SimpleNamespace(agent_id='saved', task_id=None)
        orch.registry = SimpleNamespace(get_agents_by_role=Mock(return_value=[]),
                                        materialize=AsyncMock(return_value=agent), start_agent=AsyncMock())
        orch.store = SimpleNamespace(one=AsyncMock(return_value={'agent_id': 'saved'}))
        orch.tasks = SimpleNamespace(run_legacy=AsyncMock(return_value='answer'))
        answer = await orch.send_to_role('writer', 'Continue', thread_id='thread')
        self.assertEqual('answer', answer)
        orch.registry.materialize.assert_awaited_once_with('saved')
        orch.registry.start_agent.assert_not_awaited()
        self.assertEqual(agent, orch.tasks.run_legacy.await_args.args[0])
        self.assertEqual('thread', orch.tasks.run_legacy.await_args.kwargs['thread_id'])

    async def test_archive_awaits_legacy_workspace_agent_shutdown_before_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            orch = SimpleNamespace()
            orch.store = SimpleNamespace(one=AsyncMock(side_effect=[None, {'stage': 'stopping'}]),
                                          rows=AsyncMock(side_effect=[[], [{'agent_id': 'legacy', 'cwd': str(root / 'subdir')}]]),
                                          write=AsyncMock())
            orch.tasks = SimpleNamespace(running={})
            runtime = SimpleNamespace(agent_id='legacy', _threads={}, activity={'threads': []})
            orch.registry = SimpleNamespace(agents={'legacy': runtime}, get_agent=Mock(return_value=runtime),
                                             destroy_agent=AsyncMock(side_effect=RuntimeError('shutdown blocked')))
            service = ArchiveService(orch)
            service.stage = AsyncMock()
            with patch('orchestrator.archives.capture') as capture:
                with self.assertRaisesRegex(RuntimeError, 'shutdown blocked'):
                    await service.execute_archive({'workspace_id': 'workspace', 'path': str(root)}, 'archive')
                capture.assert_not_called()
            orch.registry.destroy_agent.assert_awaited_once_with('legacy')
            self.assertTrue(root.exists())

    async def test_archive_blocks_a_persisted_agent_owned_by_another_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            record = {'owner_pid': 123, 'owner_started': 'start', 'pid': None, 'status': 'running'}
            orch = SimpleNamespace(
                store=SimpleNamespace(one=AsyncMock(side_effect=[None, record, {'stage': 'stopping'}]),
                                      rows=AsyncMock(side_effect=[[], [{'agent_id': 'external', 'cwd': str(root)}]]),
                                      write=AsyncMock()),
                tasks=SimpleNamespace(running={}),
                registry=SimpleNamespace(agents={}, get_agent=Mock(return_value=None), destroy_agent=AsyncMock()))
            service = ArchiveService(orch)
            service.stage = AsyncMock()
            with patch('orchestrator.archives.owner_alive', return_value=True), \
                 patch('orchestrator.archives.capture') as capture:
                with self.assertRaisesRegex(RuntimeError, 'another service'):
                    await service.execute_archive({'workspace_id': 'workspace', 'path': str(root)}, 'archive')
                capture.assert_not_called()
            orch.registry.destroy_agent.assert_not_awaited()
            self.assertTrue(root.exists())

    async def test_materialize_fills_missing_legacy_role_configuration(self):
        registry = AgentRegistry(SimpleNamespace(base_port=15000, max_agents=10),
                                 SimpleNamespace(one=AsyncMock(return_value={
                                     'agent_id': 'saved', 'role': 'writer', 'backend': 'codex',
                                     'config': {}, 'retired_at': None, 'owner_pid': None,
                                     'owner_started': None, 'pid': None, 'task_id': None})),
                                 SimpleNamespace())
        registry.create_agent = AsyncMock()
        await registry.materialize('saved')
        role = registry.create_agent.await_args.args[0]
        self.assertEqual('writer', role.name)
        self.assertEqual('codex', role.backend)

    async def test_preparation_preserves_source_request_in_an_isolated_branch(self):
        selected = {'branch': 'feature/source', 'base': 'release', 'url': 'https://example/pr/1',
                    'provider': 'github', 'remote': 'git@github.com:org/repo.git'}
        ctx = SimpleNamespace(variables={'cwd': '/checkout', 'workspace_resolved': True,
                                         'publication_source': selected}, run_id='run')
        async def run(root, *args):
            if args[:2] == ('git', 'rev-parse'):
                return '/checkout' if '--show-toplevel' in args else 'sha'
            if args[:2] == ('git', 'remote'):
                return selected['remote']
            if args[:2] == ('git', 'branch'):
                return 'orch/isolated'
            if args[:2] == ('git', 'rev-list'):
                return 'sha'
            return ''
        with patch('orchestrator.workflows.merge_request._run', side_effect=run), \
             patch('orchestrator.workflows.merge_request._open_requests', AsyncMock(return_value=[
                 {'branch': selected['branch'], 'base': selected['base'], 'url': selected['url']}])) as requests:
            result = await PrepareMergeRequestStep('prepare').execute(ctx)
        self.assertTrue(result.success)
        self.assertEqual('feature/source', result.output['mr_branch'])
        self.assertEqual('release', result.output['mr_base'])
        self.assertEqual('orch/isolated', result.output['mr_checkout_branch'])
        self.assertEqual(selected['url'], result.output['mr_url'])
        self.assertEqual('feature/source', requests.await_args.args[-1])


class PersistedLifecycleRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / 'state.db')
        await self.store.init()
        await self.store.write("INSERT INTO repositories VALUES ('repo','Repo','/repo','/repo/.git',1,'now','now')")
        for wid in ('live', 'dead'):
            await self.store.write('INSERT INTO workspaces VALUES (?,?,?,?,?,?,?,?,?)',
                                   (wid, 'repo', 'worktree', '/repo/' + wid, wid, 'sha', 'active', 'now', 'now'))
            await self.store.write('INSERT INTO tasks (task_id,workspace_id,title,prompt,kind,status,settings_json,fingerprint,created_at,updated_at,owner_pid,owner_started) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                   (wid, wid, 'Task', 'Work', 'agent', 'active', '{}', '', 'now', 'now', 123 if wid == 'live' else 456, 'start'))
        self.orch = SimpleNamespace(store=self.store)
        self.service = TaskService(self.orch)

    async def asyncTearDown(self):
        await self.service.shutdown()
        await self.store.close()
        self.temp.cleanup()

    async def test_reconcile_retains_live_lease_and_reclaims_only_dead_owner(self):
        for wid in ('live', 'dead'):
            await self.store.write('INSERT INTO workspace_leases VALUES (?,?,?,?)',
                                   (wid, wid, 123 if wid == 'live' else 456, 'start'))
            await self.store.create_input(wid, wid, 'Continue?')
        with patch('orchestrator.workspaces.owner_alive', side_effect=lambda pid, started: pid == 123):
            await WorkspaceService(self.orch).reconcile()
        self.assertIsNotNone(await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='live'"))
        self.assertIsNone(await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='dead'"))
        self.assertEqual('waiting_input', (await self.store.task('live'))['status'])
        self.assertEqual('interrupted', (await self.store.task('dead'))['status'])
        self.assertEqual(1, len(await self.store.pending_inputs('live')))
        self.assertEqual([], await self.store.pending_inputs('dead'))

    async def test_dead_service_with_live_backend_keeps_its_workspace_fence(self):
        await self.store.save_agent(AgentRecord('orphan', 'writer', 'codex', pid=789, status='running'))
        await self.store.write("UPDATE agents SET task_id='dead',owner_pid=456,owner_started='start' WHERE agent_id='orphan'")
        await self.store.write("INSERT INTO workspace_leases VALUES ('dead','dead',456,'start')")
        with patch('orchestrator.workspaces.owner_alive', return_value=False), \
             patch('orchestrator.workspaces.process_group_alive', return_value=True):
            await WorkspaceService(self.orch).reconcile()
        self.assertIsNotNone(await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='dead'"))
        self.assertEqual('active', (await self.store.task('dead'))['status'])

    async def test_lease_claim_rejects_archiving_workspaces_and_other_tasks(self):
        service = WorkspaceService(self.orch)
        await self.store.write("UPDATE workspaces SET status='archiving' WHERE workspace_id='live'")
        with self.assertRaisesRegex(Conflict, 'archived'):
            await service.claim_lease('live', 'live', 123, 'start')
        await self.store.write("UPDATE workspaces SET status='active' WHERE workspace_id='live'")
        await service.claim_lease('live', 'live', 123, 'start')
        with self.assertRaisesRegex(Conflict, 'another task'):
            await service.claim_lease('live', 'dead', 456, 'start')
        self.assertEqual('live', (await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='live'"))['task_id'])

    async def test_legacy_execution_holds_a_lease_until_backend_shutdown(self):
        self.orch.workspaces = WorkspaceService(self.orch)
        self.orch.workspaces.register = AsyncMock()
        self.orch.config = SimpleNamespace(effective_project_dir='/repo/live')
        await self.store.save_agent(AgentRecord('legacy', 'writer', 'codex'))
        agent = SimpleNamespace(agent_id='legacy', task_id=None, _threads={}, role=RoleConfig(name='writer'))
        async def stop(agent_id):
            self.assertIsNotNone(await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='live'"))
        self.orch.registry = SimpleNamespace(start_agent=AsyncMock(), stop_agent=AsyncMock(side_effect=stop))
        async def operation(cwd):
            self.assertEqual('/repo/live', cwd)
            lease = await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='live'")
            self.assertEqual(agent.task_id, lease['task_id'])
            return 'answer'
        with patch('orchestrator.tasks._run', AsyncMock(return_value='/repo/live')):
            self.assertEqual('answer', await self.service.run_legacy(agent, 'Work', operation))
        self.assertIsNone(await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='live'"))
        self.assertIsNone(agent.task_id)
        self.orch.registry.stop_agent.assert_awaited_once_with('legacy')

    async def test_cancellation_serializes_with_message_acceptance(self):
        await self.store.write('UPDATE tasks SET owner_pid=?,owner_started=? WHERE task_id=?',
                               (self.service.owner_pid, self.service.owner_started, 'live'))
        cancelling = asyncio.Event()
        finish = asyncio.Event()
        original = self.store.task_status
        async def status(*args, **kwargs):
            result = await original(*args, **kwargs)
            if args[1] == 'cancelled':
                cancelling.set()
                await finish.wait()
            return result
        with patch.object(self.store, 'task_status', side_effect=status):
            cancellation = asyncio.create_task(self.service.cancel('live'))
            await cancelling.wait()
            message = asyncio.create_task(self.service.send_message('live', 'Late message'))
            await asyncio.sleep(0)
            self.assertFalse(message.done())
            finish.set()
            await cancellation
            with self.assertRaises(Conflict):
                await message
        self.assertEqual('cancelled', (await self.store.task('live'))['status'])
        self.assertNotIn('live', self.service.running)

    async def test_cancel_awaits_input_handler_and_preserves_terminal_status(self):
        await self.store.write('UPDATE tasks SET owner_pid=?,owner_started=? WHERE task_id=?',
                               (self.service.owner_pid, self.service.owner_started, 'live'))
        ready = asyncio.Event()
        original = self.store.create_input
        async def create(*args, **kwargs):
            await original(*args, **kwargs)
            ready.set()
        with patch.object(self.store, 'create_input', side_effect=create):
            handler = asyncio.create_task(self.service.request_input('live', 'Continue?', timeout=30))
            await ready.wait()
            await self.service.cancel('live')
        self.assertTrue(handler.cancelled())
        self.assertEqual('cancelled', (await self.store.task('live'))['status'])
        self.assertEqual([], await self.store.pending_inputs('live'))
        with self.assertRaises(asyncio.CancelledError):
            await self.service.request_input('live', 'Late request')
        self.assertEqual('cancelled', (await self.store.task('live'))['status'])

    async def test_input_cleanup_preserves_interruption(self):
        ready = asyncio.Event()
        original = self.store.create_input
        async def create(*args, **kwargs):
            await original(*args, **kwargs)
            ready.set()
        with patch.object(self.store, 'create_input', side_effect=create):
            handler = asyncio.create_task(self.service.request_input('live', 'Continue?', timeout=30))
            await ready.wait()
            await self.store.task_status('live', 'interrupted', 'Service stopped')
            handler.cancel()
            await asyncio.gather(handler, return_exceptions=True)
        self.assertEqual('interrupted', (await self.store.task('live'))['status'])
        self.assertEqual([], await self.store.pending_inputs('live'))

    async def test_publication_is_persisted_before_checkout_provisioning(self):
        settings = {'isolate': True, 'workflow': 'feature', 'variables': {'create_mr': True}}
        await self.store.write("UPDATE tasks SET kind='workflow',settings_json=?,owner_pid=NULL,owner_started=NULL WHERE task_id='live'", (json.dumps(settings),))
        self.orch.workflows = {'feature': SimpleNamespace(publication_policy='optional')}
        selected = {'branch': 'source', 'base': 'release', 'url': 'existing'}
        async def provision(task_id, isolate):
            saved = await self.store.task(task_id)
            self.assertEqual(selected, saved['settings']['variables']['publication_source'])
            raise RuntimeError('fixture: stop after publication selection')
        self.orch.workspaces = SimpleNamespace(ensure_task_workspace=AsyncMock(side_effect=provision))
        self.orch.registry = SimpleNamespace(get_agent=Mock())
        with patch('orchestrator.tasks.resolve_publication_identity', AsyncMock(return_value=selected)) as resolve:
            await self.service.execute('live')
        resolve.assert_awaited_once_with('/repo/live', {'create_mr': True, 'publication_source': selected})
        self.orch.workspaces.ensure_task_workspace.assert_awaited_once_with('live', True)


class ProcessOwnerTests(unittest.TestCase):
    def test_pid_reuse_does_not_keep_a_stale_lease_alive(self):
        with patch('orchestrator.processes.process_identity', return_value='new start'):
            self.assertFalse(owner_alive(123, 'old start'))
            self.assertTrue(owner_alive(123, 'new start'))
        self.assertTrue(owner_alive(None, None))


class ArchiveIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_staged_only_content_blocks_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory).resolve() / 'main'
            root = Path(directory).resolve() / 'worktree'
            main.mkdir()
            def git(cwd, *args):
                return subprocess.check_output(['git', *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()
            git(main, 'init')
            git(main, 'config', 'user.email', 'test@example.com')
            git(main, 'config', 'user.name', 'Test')
            (main / 'file').write_text('committed')
            git(main, 'add', 'file')
            git(main, 'commit', '-m', 'Initial')
            git(main, 'worktree', 'add', '-b', 'task', str(root))
            (root / 'file').write_text('staged only')
            git(root, 'add', 'file')
            (root / 'file').write_text('working file')
            repository = {'main_path': str(main), 'common_dir': str(main / '.git')}
            orch = SimpleNamespace(
                store=SimpleNamespace(one=AsyncMock(side_effect=[None, repository, {'stage': 'stopping'}]),
                                      rows=AsyncMock(side_effect=[[], []]), write=AsyncMock()),
                tasks=SimpleNamespace(running={}), registry=SimpleNamespace(agents={}),
                workspaces=SimpleNamespace(lock=Mock(return_value=asyncio.Lock())))
            service = ArchiveService(orch)
            service.stage = AsyncMock()
            with patch('orchestrator.archives.capture') as capture:
                with self.assertRaisesRegex(Conflict, 'Staged index capture'):
                    await service.execute_archive({'workspace_id': 'workspace', 'repository_id': 'repo', 'path': str(root)}, 'archive')
                capture.assert_not_called()
            self.assertTrue(root.is_dir())
            self.assertEqual('staged only', git(root, 'show', ':file'))
            self.assertEqual('working file', (root / 'file').read_text())
