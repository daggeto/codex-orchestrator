import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orchestrator.api.submissions import accept
from orchestrator.config import OrchestratorConfig
from orchestrator.core import Orchestrator
from orchestrator.roles.loader import RoleConfig
from orchestrator.tasks import Conflict
from orchestrator.workflows.merge_request import (
    CreateMergeRequestStep, PrepareMergeRequestStep, PrepareWorktreeStep, _run,
)
from orchestrator.workflows.builtins import feature_workflow
from orchestrator.workflows.steps import AssignStep


class WorktreeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        await _run(self.root, 'git', 'init', '-b', 'main')
        await _run(self.root, 'git', 'config', 'user.email', 'test@example.com')
        await _run(self.root, 'git', 'config', 'user.name', 'Test')
        Path(self.root, 'file').write_text('original')
        await _run(self.root, 'git', 'add', 'file')
        await _run(self.root, 'git', 'commit', '-m', 'baseline')

        async def acquire_thread(agent, step, **kwargs):
            return (await agent.start_thread(**kwargs)).thread_id

        self.ctx = SimpleNamespace(run_id='test', variables={'cwd': self.root, 'create_mr': False},
                                   acquire_thread=acquire_thread, persist_step_thread=AsyncMock())

    async def asyncTearDown(self):
        checkout = self.ctx.variables.get('task_worktree')
        if checkout and checkout != self.root:
            await _run(self.root, 'git', 'worktree', 'remove', '--force', checkout)
            Path(checkout).parent.rmdir()
        self.tmp.cleanup()

    async def test_local_worktree_preserves_dirty_original_and_reaches_review(self):
        Path(self.root, 'file').write_text('unrelated')
        result = await PrepareMergeRequestStep('prepare').execute(self.ctx)
        self.assertTrue(result.success)
        self.ctx.variables.update(result.output)
        checkout = self.ctx.variables['cwd']
        self.assertNotEqual(checkout, self.root)
        self.assertEqual(Path(checkout, 'file').read_text(), 'original')
        self.assertEqual(Path(self.root, 'file').read_text(), 'unrelated')
        agent = SimpleNamespace(start_thread=AsyncMock(return_value=SimpleNamespace(thread_id='thread')),
                                send_message=AsyncMock(return_value=SimpleNamespace(
                                    ok=True, full_text='Committed', files_changed=['file'], commands_run=[], error=None)))
        self.ctx.get_agent_for_role = lambda role: agent
        result = await AssignStep('implement', role='implementer', message_template='Fix', cwd='{cwd}').execute(self.ctx)
        self.assertTrue(result.success)
        self.assertEqual(result.output['task_commit'], await _run(checkout, 'git', 'rev-parse', 'HEAD'))
        self.assertIn(checkout, agent.send_message.call_args.args[1])
        agent.send_message.return_value.full_text = 'APPROVED'
        self.ctx.variables.update(plan='Fix plan', implementation='Committed', implementation_files=['file'])
        result = await feature_workflow().get_step('review').execute(self.ctx)
        self.assertTrue(result.success)
        self.assertEqual(result.output['review_commit'], await _run(checkout, 'git', 'rev-parse', 'HEAD'))
        self.assertEqual(agent.start_thread.call_args.kwargs['cwd'], checkout)
        self.assertIn(f"git diff {self.ctx.variables['task_baseline']}..HEAD", agent.send_message.call_args.args[1])
        self.assertEqual(await _run(self.root, 'git', 'branch', '--show-current'), 'main')

    async def test_existing_request_keeps_publication_branch(self):
        await _run(self.root, 'git', 'remote', 'add', 'origin', 'https://github.com/example/repo.git')
        real_run = _run
        pushes = []

        async def run(cwd, *args):
            if args[:2] == ('git', 'push'):
                pushes.append((cwd, args[-1]))
                return ''
            if args[:2] == ('git', 'fetch'):
                await real_run(cwd, 'git', 'update-ref', 'refs/remotes/origin/release', 'HEAD')
                return ''
            return await real_run(cwd, *args)

        self.ctx.variables['create_mr'] = True
        with patch('orchestrator.workflows.merge_request._run', run), \
             patch('orchestrator.workflows.merge_request.shutil.which', return_value='/bin/gh'), \
             patch('orchestrator.workflows.merge_request._open_requests', AsyncMock(return_value=[
                 {'url': 'https://github.com/example/repo/pull/1', 'base': 'release', 'branch': 'main'}])):
            result = await PrepareMergeRequestStep('prepare').execute(self.ctx)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.output['mr_branch'], 'main')
        self.assertEqual(result.output['mr_checkout_branch'], 'orch/task-test')
        self.assertEqual(result.output['mr_repo'], self.ctx.variables['cwd'])
        self.assertEqual(await _run(self.root, 'git', 'branch', '--show-current'), 'main')
        self.ctx.variables.update(result.output)
        self.ctx.variables.update(plan='Fix plan', implementation='Committed', implementation_files=['file'])
        agent = SimpleNamespace(start_thread=AsyncMock(return_value=SimpleNamespace(thread_id='review-thread')),
                                send_message=AsyncMock(return_value=SimpleNamespace(ok=True, full_text='APPROVED')))
        self.ctx.get_agent_for_role = lambda role: agent
        review = await feature_workflow().get_step('review').execute(self.ctx)
        self.assertTrue(review.success, review.error)
        prompt = agent.send_message.call_args.args[1]
        self.assertIn('Selected PR/MR: https://github.com/example/repo/pull/1', prompt)
        self.assertIn('Publication source branch: main', prompt)
        self.assertIn('Task checkout branch: orch/task-test', prompt)
        self.assertIn('Selected target branch: release', prompt)
        self.assertIn('git diff origin/release...HEAD', prompt)
        checkout = result.output['mr_repo']
        Path(checkout, 'file').write_text('implementation')
        await _run(checkout, 'git', 'commit', '-am', 'implementation')
        self.ctx.variables.update(review_decision='approved', review_commit=await _run(checkout, 'git', 'rev-parse', 'HEAD'))
        with patch('orchestrator.workflows.merge_request._run', run), \
             patch('orchestrator.workflows.merge_request._open_requests', AsyncMock(return_value=[
                 {'url': 'https://github.com/example/repo/pull/1', 'base': 'release', 'branch': 'main'}])):
            published = await CreateMergeRequestStep('publish').execute(self.ctx)
        self.assertTrue(published.success, published.error)
        self.assertEqual(pushes, [(checkout, 'HEAD:refs/heads/main')])
        self.assertEqual(Path(self.root, 'file').read_text(), 'original')

    async def test_worktree_failure_stops_without_changing_source(self):
        with patch('orchestrator.workflows.merge_request.prepare_worktree', AsyncMock(side_effect=RuntimeError('denied'))):
            result = await PrepareWorktreeStep('prepare').execute(self.ctx)
        self.assertFalse(result.success)
        self.assertIn('denied', result.error)
        self.assertEqual(self.ctx.variables['cwd'], self.root)

    async def test_publication_rejects_commit_changed_after_review(self):
        self.ctx.variables.update(mr_status='prepared', review_decision='approved', mr_repo=self.root,
                                  mr_branch='main', mr_base='main', task_worktree=self.root, review_commit='old')
        result = await CreateMergeRequestStep('publish').execute(self.ctx)
        self.assertFalse(result.success)
        self.assertIn('changed since approval', result.error)


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name, 'project').resolve()
        self.directory.mkdir()
        self.variables = {'cwd': str(self.directory), 'task_description': 'Fix bug', 'create_mr': False}
        self.orch = Orchestrator(OrchestratorConfig(data_dir=Path(self.tmp.name) / 'data'))
        self.store = self.orch.store
        self.addAsyncCleanup(self.store.close)
        await self.store.init()
        self.addAsyncCleanup(self.orch.tasks.shutdown)
        await self.store.register_project(str(self.directory))
        await self.store.write("INSERT INTO repositories VALUES ('repo','Repo',?,'git',1,'now','now')", (str(self.directory),))
        await self.store.write("INSERT INTO workspaces VALUES ('main','repo','main',?,'main','sha','active','now','now')", (str(self.directory),))
        self.orch.workflows = {'feature': SimpleNamespace(name='feature', publication_policy='optional',
                                                        required_roles=['implementer'], role_overrides={})}
        self.orch.roles = {'implementer': RoleConfig(name='implementer')}
        self.orch.workspaces.register = AsyncMock()
        self.orch.tasks.execute = AsyncMock()
        self.request = SimpleNamespace(app={'orch': self.orch}, match_info={'name': 'feature'},
                                       json=AsyncMock(return_value={'run_id': 'retry', 'variables': self.variables}))

    async def save_acceptance(self):
        response = await accept(self.request)
        self.assertEqual(response.status, 202)
        self.task_id = json.loads(response.body)['task_id']
        self.orch.workspaces.register.assert_awaited_once_with(str(self.directory))
        self.orch.workspaces.register.reset_mock()

    async def assert_retry(self):
        response = await accept(self.request)
        self.assertEqual(response.status, 202)
        self.assertEqual(json.loads(response.body)['task_id'], self.task_id)
        self.orch.workspaces.register.assert_not_awaited()
        self.assertEqual(len(await self.store.rows('SELECT * FROM tasks')), 1)
        self.assertEqual(len(await self.store.rows('SELECT * FROM task_messages')), 1)

    async def test_retry_survives_removed_directory_and_registration(self):
        await self.save_acceptance()
        self.directory.rmdir()
        await self.store.write('DELETE FROM projects')
        await self.assert_retry()
        self.variables['task_description'] = 'Different task'
        with self.assertRaises(Conflict):
            await accept(self.request)

    async def test_retry_does_not_check_access(self):
        await self.save_acceptance()
        with patch('orchestrator.api.projects.os.access', return_value=False):
            await self.assert_retry()

    async def test_legacy_retry_survives_removed_directory(self):
        await self.save_acceptance()
        await self.store.write('UPDATE tasks SET submission_workspace_id=NULL WHERE task_id=?', (self.task_id,))
        self.directory.rmdir()
        await self.assert_retry()

    async def test_new_submission_still_validates_directory(self):
        self.directory.rmdir()
        with self.assertRaisesRegex(ValueError, 'Directory does not exist'):
            await accept(self.request)
        self.orch.workspaces.register.assert_not_awaited()
        self.assertEqual(await self.store.rows('SELECT * FROM tasks'), [])

    async def test_saved_path_spellings_retry_after_directory_removal(self):
        # The task fingerprint retains the submitted spelling, even though
        # workspace lookup canonicalizes it. A retry must repeat that payload.
        for suffix in ('/', '/.', '/../project'):
            with self.subTest(suffix=suffix):
                self.variables['cwd'] = str(self.directory) + suffix
                self.request.json.return_value['run_id'] = 'retry-' + str(len(suffix))
                self.directory.mkdir(exist_ok=True)
                await self.save_acceptance()
                self.directory.rmdir()
                response = await accept(self.request)
                self.assertEqual(response.status, 202)
                self.assertEqual(json.loads(response.body)['task_id'], self.task_id)
                self.orch.workspaces.register.assert_not_awaited()
                self.variables['cwd'] = str(self.directory)
                with self.assertRaises(Conflict):
                    await accept(self.request)
        self.assertEqual(len(await self.store.rows('SELECT * FROM tasks')), 3)
        self.assertEqual(len(await self.store.rows('SELECT * FROM task_messages')), 3)

    async def test_saved_path_spelling_retry_does_not_check_access(self):
        self.variables['cwd'] = str(self.directory) + '/'
        await self.save_acceptance()
        with patch('orchestrator.api.projects.os.access', return_value=False):
            await self.assert_retry()
