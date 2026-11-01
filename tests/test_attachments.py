"""Attachment ownership, retry and deletion contracts. No agent backend is launched."""
import asyncio
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from PIL import Image

from orchestrator.config import OrchestratorConfig
from orchestrator.core import Orchestrator
from orchestrator.tasks import Conflict


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.orch = Orchestrator(OrchestratorConfig(data_dir=self.base / 'data', attachments_enabled=True))
        self.store = self.orch.store
        await self.store.init()
        await self.store.write("INSERT INTO repositories VALUES ('repo','Repo','/repo','/repo/.git',1,'now','now')")
        await self.store.write("INSERT INTO workspaces VALUES ('main','repo','main','/repo','main','sha','active','now','now')")
        await self.store.write("INSERT INTO workspaces VALUES ('worktree','repo','worktree','/repo/task','task','sha','active','now','now')")
        self.service = self.orch.attachments
        self.orch.tasks.execute = AsyncMock()

    async def asyncTearDown(self):
        await self.orch.tasks.shutdown()
        await self.store.close()
        self.temp.cleanup()

    async def upload(self, name='notes.txt', content=b'context', draft=None, aid='upload'):
        draft = draft or await self.service.create_draft('main')
        async def chunks():
            yield content[:2]
            yield content[2:]
        row = await self.service.upload(draft['draft_id'], aid, name, chunks())
        return draft, row

    async def submit(self, draft, row, key='task'):
        return await self.orch.tasks.submit('main', '', 'agent', isolation='never', idempotency_key=key,
            attachment_ids=[row['attachment_id']], draft_id=draft['draft_id'])

    async def test_attachment_only_acceptance_and_response_loss_are_atomic(self):
        draft, row = await self.upload()
        first = await self.submit(draft, row)
        second = await self.submit(draft, row)
        self.assertEqual(first['task_id'], second['task_id'])
        self.assertEqual(1, len(await self.store.rows('SELECT * FROM task_messages')))
        self.assertEqual(1, len(await self.store.rows('SELECT * FROM message_attachments')))
        await asyncio.sleep(0)
        self.orch.tasks.execute.assert_awaited_once()
        self.assertEqual(first['task_id'], (await self.service.listing(first['task_id']))[0]['task_id'])

    async def test_same_names_never_overwrite_and_upload_retries_verify_content(self):
        draft, first = await self.upload('../../same.txt', b'one')
        _, second = await self.upload('same.txt', b'two', draft, 'second')
        self.assertEqual(first['filename'], second['filename'])
        self.assertNotEqual(first['storage_key'], second['storage_key'])
        _, retry = await self.upload('../../same.txt', b'one', draft)
        self.assertEqual(first['sha256'], retry['sha256'])
        with self.assertRaises(Conflict):
            await self.upload('same.txt', b'changed', draft)
        self.assertEqual(b'one', self.service.path(first['storage_key']).read_bytes())

    async def test_binding_rejects_partial_set_and_rolls_back_task(self):
        draft, row = await self.upload()
        with self.assertRaises(Conflict):
            await self.orch.tasks.submit('main', 'Use files', 'agent', isolation='never',
                attachment_ids=[row['attachment_id'], 'missing'], draft_id=draft['draft_id'])
        self.assertEqual([], await self.store.tasks('main'))
        self.assertIsNone((await self.service.draft(draft['draft_id']))['files'][0]['task_id'])
        self.orch.tasks.execute.assert_not_called()

    async def test_isolation_and_purge_follow_task_owner_including_archived_tasks(self):
        draft, row = await self.upload()
        task = await self.submit(draft, row)
        await self.store.write("UPDATE tasks SET workspace_id='worktree',status='completed',archived_at='now'")
        await self.service.purge_workspace('main')
        self.assertTrue(self.service.path(row['storage_key']).is_file())
        await self.service.purge_workspace('worktree')
        await self.service.purge_workspace('worktree')
        self.assertFalse(self.service.path(row['storage_key']).exists())
        self.assertEqual('deleted', (await self.service.listing(task['task_id']))[0]['state'])

    async def test_expiry_protects_live_writer_and_later_removes_abandoned_files(self):
        draft, row = await self.upload()
        await self.store.write("UPDATE attachment_drafts SET expires_at='2000'")
        await self.service.expire_drafts()
        self.assertFalse(self.service.path(row['storage_key']).exists())
        self.assertEqual('expired', (await self.service.draft(draft['draft_id']))['state'])

    async def test_image_validation_and_stream_limit_are_enforced(self):
        image = io.BytesIO()
        Image.new('RGB', (2, 3)).save(image, 'PNG')
        _, row = await self.upload('image.txt', image.getvalue())
        self.assertEqual(('image/png', 2, 3), (row['media_type'], row['width'], row['height']))
        self.orch.config.attachment_max_file_bytes = 2
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            await self.upload(aid='large')
        failed = await self.store.one("SELECT * FROM attachments WHERE attachment_id='large'")
        self.assertEqual('failed', failed['state'])
        self.assertFalse(self.service.path(failed['storage_key'], True).exists())

    async def test_restart_uses_independent_storage_and_requires_explicit_omission(self):
        draft, row = await self.upload()
        task = await self.submit(draft, row)
        groups = await self.service.copy_for_restart(task['task_id'], 'main')
        copied = (await self.service.draft(groups[0][0]))['files'][0]
        self.assertNotEqual(row['storage_key'], copied['storage_key'])
        await self.service.delete(row['attachment_id'])
        self.assertTrue(self.service.path(copied['storage_key']).is_file())
        with self.assertRaisesRegex(Conflict, 'explicit omission'):
            await self.service.copy_for_restart(task['task_id'], 'main')
        self.assertEqual([], await self.service.copy_for_restart(task['task_id'], 'main', True))

    async def test_followup_retry_recovers_before_busy_or_bound_checks(self):
        task = await self.orch.tasks.submit('main', 'Start', 'agent', isolation='never')
        await asyncio.gather(*list(self.orch.tasks.running.values()))
        await self.store.write("UPDATE tasks SET status='active'")
        draft = await self.service.create_draft('main', task['task_id'])
        draft, row = await self.upload(draft=draft)
        args = (task['task_id'], '', [row['attachment_id']], draft['draft_id'], 'message')
        first = await self.orch.tasks.send_message(*args)
        second = await self.orch.tasks.send_message(*args)
        self.assertEqual(first, second)
        self.assertEqual(1, len(await self.store.rows("SELECT * FROM task_messages WHERE origin='followup'")))

    async def test_input_response_binds_before_waiter_wakes_and_retries_after_resolution(self):
        task = await self.orch.tasks.submit('main', 'Start', 'agent', isolation='never')
        await asyncio.gather(*list(self.orch.tasks.running.values()))
        await self.store.create_input('question', task['task_id'], 'Choose', 'backend_input', options=[{'id': 'choice'}])
        future = asyncio.get_running_loop().create_future()
        self.orch.tasks.input_waiters['question'] = future
        draft = await self.service.create_draft('main', task['task_id'])
        draft, row = await self.upload(draft=draft)
        with self.assertRaises(ValueError):
            await self.orch.tasks.resolve_input('question', {}, [row['attachment_id']], draft['draft_id'])
        answer = {'answers': {'choice': {'answers': ['yes']}}}
        await self.orch.tasks.resolve_input('question', answer, [row['attachment_id']], draft['draft_id'])
        self.assertEqual(answer, future.result())
        self.assertEqual(task['task_id'], (await self.service.listing(task['task_id']))[0]['task_id'])
        self.orch.tasks.input_waiters.clear()
        await self.orch.tasks.resolve_input('question', answer, [row['attachment_id']], draft['draft_id'])
        self.assertEqual(1, len(await self.store.rows("SELECT * FROM task_messages WHERE origin='input_response'")))

    async def test_failed_purge_retains_progress_and_retry_finishes(self):
        from unittest.mock import patch
        draft, row = await self.upload()
        await self.submit(draft, row)
        original = Path.unlink
        def denied(path, *args, **kwargs):
            if path.name == row['storage_key']:
                raise PermissionError('fixture denial')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', denied):
            with self.assertRaisesRegex(OSError, 'cleanup failed'):
                await self.service.purge_workspace('main')
        failed = await self.store.one('SELECT * FROM attachments WHERE attachment_id=?', (row['attachment_id'],))
        self.assertEqual('deleting', failed['state'])
        self.assertIn('fixture denial', failed['error'])
        await self.service.purge_workspace('main')
        self.assertEqual('deleted', (await self.service.draft(draft['draft_id']))['files'][0]['state'])

    async def test_storage_under_checkout_is_rejected(self):
        self.service.root = Path('/repo/attachments')
        with self.assertRaisesRegex(Conflict, 'outside every checkout'):
            await self.service.create_draft('main')

    async def test_live_upload_writer_is_never_expired(self):
        from orchestrator.processes import process_identity
        import os
        draft, row = await self.upload()
        await self.store.write("UPDATE attachments SET state='uploading',writer_pid=?,writer_started=?", (os.getpid(), process_identity(os.getpid())))
        await self.store.write("UPDATE attachment_drafts SET expires_at='2000'")
        await self.service.expire_drafts()
        self.assertTrue(self.service.path(row['storage_key']).exists())
        self.assertEqual('open', (await self.service.draft(draft['draft_id']))['state'])
