"""Task-owned resources. Managed bytes never enter a checkout or its archive."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import uuid
import warnings

from orchestrator.processes import owner_alive, process_identity
from orchestrator.state.models import now
from orchestrator.tasks import Conflict

logger = logging.getLogger(__name__)


async def one(db, sql, params=()):
    return await (await db.execute(sql, params)).fetchone()


def identities(values):
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values):
        raise ValueError('attachment_ids must be an ordered list of unique IDs')
    return values


class AttachmentService:
    def __init__(self, orch):
        self.orch = orch
        self.store = orch.store
        self.config = orch.config
        self.root = (self.config.data_dir / 'attachments').resolve()
        self._cleanup_task = None

    @property
    def limits(self):
        return {'enabled': self.config.attachments_enabled, 'files_per_message': self.config.attachment_files_per_message,
                'max_file_bytes': self.config.attachment_max_file_bytes,
                'max_task_bytes': self.config.attachment_max_task_bytes,
                'draft_hours': self.config.attachment_draft_hours}

    async def check_storage(self):
        for workspace in await self.store.rows('SELECT path FROM workspaces'):
            if self.root.is_relative_to(Path(workspace['path']).resolve()):
                raise Conflict('Attachment data_dir must be outside every checkout')
        if self.root.is_relative_to((self.config.data_dir / 'archives').resolve()):
            raise Conflict('Attachment storage must be outside archives')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def path(self, key, staging=False):
        if not re.fullmatch(r'[a-f0-9]{32}', key):
            raise ValueError('Invalid managed storage key')
        path = self.root / (key + ('.part' if staging else ''))
        if path.is_symlink() or path.resolve().parent != self.root:
            raise Conflict('Managed attachment path changed')
        return path

    @staticmethod
    async def active(db, workspace_id, task_id=None):
        if task_id:
            task = await one(db, 'SELECT workspace_id,archived_at FROM tasks WHERE task_id=?', (task_id,))
            if not task or task['archived_at']:
                raise Conflict('Task is unavailable for attachment writes')
            workspace_id = task['workspace_id']
        workspace = await one(db, 'SELECT status FROM workspaces WHERE workspace_id=?', (workspace_id,))
        if not workspace or workspace['status'] != 'active':
            raise Conflict('Workspace is unavailable for attachments')

    async def create_draft(self, workspace_id, task_id=None):
        if not self.config.attachments_enabled:
            raise Conflict('Attachments await source approval. Enable attachments_enabled in config.toml after cleanup and delivery review.')
        await self.check_storage()
        did = uuid.uuid4().hex
        expires = (datetime.now(timezone.utc) + timedelta(hours=self.config.attachment_draft_hours)).isoformat()
        async with self.store.transaction() as db:
            await self.active(db, workspace_id, task_id)
            if task_id:
                task = await one(db, 'SELECT workspace_id FROM tasks WHERE task_id=?', (task_id,))
                if task['workspace_id'] != workspace_id:
                    raise Conflict('Draft workspace does not own this task')
            await db.execute('INSERT INTO attachment_drafts VALUES (?,?,?,?,?,?,?)',
                             (did, workspace_id, task_id, 'open', now(), now(), expires))
        return await self.draft(did)

    async def draft(self, did):
        draft = await self.store.one('SELECT * FROM attachment_drafts WHERE draft_id=?', (did,))
        if not draft:
            raise LookupError('Attachment draft not found')
        return {**draft, 'files': await self.store.rows('SELECT * FROM attachments WHERE draft_id=? ORDER BY created_at,attachment_id', (did,)), 'limits': self.limits}

    async def open_draft(self, db, did):
        draft = await one(db, 'SELECT * FROM attachment_drafts WHERE draft_id=?', (did,))
        if not draft or draft['state'] != 'open' or draft['expires_at'] <= now():
            raise Conflict('Attachment draft expired or was submitted; select files again')
        await self.active(db, draft['workspace_id'], draft['task_id'])
        return draft

    async def upload(self, did, aid, filename, chunks):
        if not self.config.attachments_enabled:
            raise Conflict('Attachment uploads are disabled pending source approval')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', aid):
            raise ValueError('Invalid upload ID')
        if not isinstance(filename, str) or not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
            raise ValueError('File needs a name of at most 255 characters without control characters')
        # Names are display metadata only, never filesystem paths.
        filename = filename.replace('\\', '/').rsplit('/', 1)[-1]
        if filename in {'', '.', '..'}:
            raise ValueError('Invalid filename')
        await self.check_storage()
        previous = None
        async with self.store.transaction() as db:
            previous = await one(db, 'SELECT * FROM attachments WHERE attachment_id=?', (aid,))
            if previous and (previous['draft_id'] != did or previous['filename'] != filename):
                raise Conflict(f'{filename}: upload ID belongs to another selection')
            if previous and previous['state'] == 'ready':
                draft = await one(db, 'SELECT * FROM attachment_drafts WHERE draft_id=?', (did,))
                await self.active(db, draft['workspace_id'], previous['task_id'])
                # Consume and verify a retry, even after adoption.
            else:
                await self.open_draft(db, did)
                if previous and (previous['task_id'] or previous['state'] in {'deleted', 'deleting'}):
                    raise Conflict(f'{filename}: select the file again with a new upload ID')
                if previous and previous['state'] == 'uploading' and owner_alive(previous['writer_pid'], previous['writer_started']):
                    raise Conflict(f'{filename}: upload is still in progress')
                count = await one(db, "SELECT COUNT(*),COALESCE(SUM(byte_count),0) FROM attachments WHERE draft_id=? AND state NOT IN ('deleted','failed')", (did,))
                if (not previous or previous['state'] == 'failed') and count[0] >= self.config.attachment_files_per_message:
                    raise ValueError(f'{filename}: too many files in this message')
                key = previous['storage_key'] if previous else uuid.uuid4().hex
                if previous:
                    await db.execute("UPDATE attachments SET state='uploading',error=NULL,writer_pid=?,writer_started=?,updated_at=? WHERE attachment_id=?",
                                     (os.getpid(), process_identity(os.getpid()), now(), aid))
                else:
                    await db.execute('INSERT INTO attachments (attachment_id,draft_id,filename,storage_key,state,writer_pid,writer_started,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                                     (aid, did, filename, key, 'uploading', os.getpid(), process_identity(os.getpid()), now(), now()))
        retry = previous and previous['state'] == 'ready'
        key = previous['storage_key'] if retry else key
        staging = self.path(key, True)
        size, digest = 0, hashlib.sha256()
        try:
            sink = None if retry else staging.open('wb')
            if sink:
                os.chmod(staging, 0o600)
            try:
                async for block in chunks:
                    size += len(block)
                    if size > self.config.attachment_max_file_bytes:
                        raise ValueError(f'{filename}: file exceeds {self.config.attachment_max_file_bytes // 1024 // 1024} MiB')
                    digest.update(block)
                    if sink:
                        sink.write(block)
                if sink:
                    sink.flush()
                    os.fsync(sink.fileno())
            finally:
                if sink:
                    sink.close()
            if retry:
                if size != previous['byte_count'] or digest.hexdigest() != previous['sha256']:
                    raise Conflict(f'{filename}: retry has different content; select again')
                return dict(previous)
            media, width, height = await asyncio.to_thread(self.validate_image, staging)
            async with self.store.transaction() as db:
                await self.open_draft(db, did)
                used = await one(db, "SELECT COALESCE(SUM(byte_count),0) FROM attachments WHERE draft_id=? AND state='ready'", (did,))
                if used[0] + size > self.config.attachment_max_task_bytes:
                    raise ValueError(f'{filename}: draft exceeds task quota')
                os.replace(staging, self.path(key))
                await db.execute("UPDATE attachments SET state='ready',byte_count=?,sha256=?,media_type=?,width=?,height=?,writer_pid=NULL,writer_started=NULL,updated_at=? WHERE attachment_id=?",
                                 (size, digest.hexdigest(), media, width, height, now(), aid))
        except BaseException as exc:
            if not retry:
                await self.store.write("UPDATE attachments SET state='failed',error=?,writer_pid=NULL,writer_started=NULL,updated_at=? WHERE attachment_id=?", (str(exc) or 'Upload interrupted; reselect file', now(), aid))
                for path in (staging, self.path(key)):
                    path.unlink(missing_ok=True)
            raise
        return await self.store.one('SELECT * FROM attachments WHERE attachment_id=?', (aid,))

    @staticmethod
    def validate_image(path):
        from PIL import Image, UnidentifiedImageError
        with path.open('rb') as source:
            signature = source.read(16)
        image_like = signature.startswith((b'\x89PNG', b'\xff\xd8')) or signature[:4] == b'RIFF' and signature[8:12] == b'WEBP'
        if not image_like:
            return 'application/octet-stream', None, None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as im:
                    if im.format not in {'PNG', 'JPEG', 'WEBP'} or im.width * im.height > 25_000_000 or getattr(im, 'n_frames', 1) != 1:
                        raise ValueError('Use a still PNG, JPEG or WebP image of at most 25 megapixels')
                    media, width, height = Image.MIME[im.format], im.width, im.height
                    im.verify()
                with Image.open(path) as im:
                    im.load()
                return media, width, height
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError('Image is invalid or too large; choose a smaller image') from exc

    async def bind(self, db, task_id, message_id, workspace_id, draft_id, attachment_ids):
        ids = identities(attachment_ids)
        if len(ids) > self.config.attachment_files_per_message:
            raise ValueError('Too many files in this message')
        await self.active(db, workspace_id, task_id)
        if not ids:
            return
        draft = await one(db, 'SELECT * FROM attachment_drafts WHERE draft_id=?', (draft_id,))
        if not draft or draft['task_id'] not in (None, task_id):
            raise Conflict('Draft belongs to another task')
        if not draft['task_id'] and draft['workspace_id'] != workspace_id:
            # Already-owned files may have originated in Main before isolation.
            unowned = await one(db, 'SELECT 1 FROM attachments WHERE draft_id=? AND task_id IS NULL', (draft_id,))
            if unowned:
                raise Conflict('Draft belongs to another workspace')
        used = await one(db, "SELECT COALESCE(SUM(byte_count),0) FROM attachments WHERE task_id=? AND state='ready'", (task_id,))
        total = used[0]
        for position, aid in enumerate(ids):
            row = await one(db, 'SELECT * FROM attachments WHERE attachment_id=?', (aid,))
            if not row or row['draft_id'] != draft_id or row['state'] != 'ready' or row['task_id'] not in (None, task_id):
                raise Conflict(f'{row["filename"] if row else aid}: file is unavailable, unfinished, or belongs to another task')
            if not row['task_id']:
                await self.open_draft(db, draft_id)
                total += row['byte_count']
            if total > self.config.attachment_max_task_bytes:
                raise ValueError(f'{row["filename"]}: task attachment quota exceeded')
            await db.execute('UPDATE attachments SET task_id=?,updated_at=? WHERE attachment_id=?', (task_id, now(), aid))
            await db.execute('INSERT INTO message_attachments VALUES (?,?,?)', (message_id, aid, position))
        await db.execute("UPDATE attachment_drafts SET state='submitted',updated_at=? WHERE draft_id=?", (now(), draft_id))

    async def copy_for_restart(self, source_task_id, workspace_id, omit_unavailable=False):
        source = await self.listing(source_task_id)
        unavailable = [r for r in source if r['state'] != 'ready' or not self.path(r['storage_key']).is_file()]
        if unavailable and not omit_unavailable:
            raise Conflict('Restart needs reselection or explicit omission of unavailable files: ' + ', '.join(r['filename'] for r in unavailable))
        unavailable_ids = {r['attachment_id'] for r in unavailable}
        if any(r['attachment_id'] not in unavailable_ids for r in source):
            source_task = await self.store.task(source_task_id)
            async with self.store.transaction() as db:
                await self.active(db, source_task['workspace_id'])
        groups = []
        # Preserve the per-message limit even when the source has many messages.
        for row in source:
            if row['attachment_id'] in unavailable_ids:
                continue
            if not groups or len(groups[-1][1]) >= self.config.attachment_files_per_message:
                draft = await self.create_draft(workspace_id)
                groups.append((draft['draft_id'], []))
            aid = uuid.uuid4().hex
            async def chunks(path=self.path(row['storage_key'])):
                with path.open('rb') as stream:
                    while block := stream.read(64 * 1024):
                        yield block
            await self.upload(groups[-1][0], aid, row['filename'], chunks())
            groups[-1][1].append(aid)
        return groups

    async def listing(self, task_id):
        if not await self.store.task(task_id):
            raise LookupError('Task not found')
        return await self.store.rows('SELECT * FROM attachments WHERE task_id=? ORDER BY created_at,attachment_id', (task_id,))

    async def resolve_for_task(self, task_id):
        task = await self.store.task(task_id)
        workspace = await self.store.workspace_record(task['workspace_id']) if task else None
        if not workspace or workspace['status'] != 'active':
            raise Conflict('Task attachments unavailable during workspace removal')
        await self.check_storage()
        result = []
        for row in await self.listing(task_id):
            if row['state'] == 'ready':
                path = self.path(row['storage_key'])
                if not path.is_file():
                    raise Conflict(f'{row["filename"]}: attachment content is missing; reselect it')
                result.append({**row, 'path': str(path)})
        return result

    async def delete(self, aid):
        async with self.store.transaction() as db:
            row = await one(db, 'SELECT * FROM attachments WHERE attachment_id=?', (aid,))
            if not row:
                raise LookupError('Attachment not found')
            if row['state'] == 'uploading' and owner_alive(row['writer_pid'], row['writer_started']):
                raise Conflict(f'{row["filename"]}: upload still stopping; retry cleanup shortly')
            await db.execute("UPDATE attachments SET state='deleting',updated_at=? WHERE attachment_id=?", (now(), aid))
        # A failed unlink deliberately leaves a retryable deleting record.
        try:
            for path in (self.path(row['storage_key']), self.path(row['storage_key'], True)):
                path.unlink(missing_ok=True)
        except OSError as exc:
            await self.store.write('UPDATE attachments SET error=?,updated_at=? WHERE attachment_id=?', (str(exc), now(), aid))
            raise OSError(f'{row["filename"]}: attachment cleanup failed: {exc}') from exc
        await self.store.write("UPDATE attachments SET state='deleted',deleted_at=?,updated_at=?,error=NULL WHERE attachment_id=?", (now(), now(), aid))

    async def remove_selection(self, did, aid):
        async with self.store.transaction() as db:
            await self.open_draft(db, did)
            row = await one(db, 'SELECT * FROM attachments WHERE attachment_id=? AND draft_id=?', (aid, did))
            if not row or row['task_id']:
                raise Conflict('Only unsubmitted selections can be removed')
            if row['state'] == 'uploading' and owner_alive(row['writer_pid'], row['writer_started']):
                raise Conflict('Upload is still running; wait before removing it')
            await db.execute("UPDATE attachments SET state='deleting' WHERE attachment_id=?", (aid,))
        await self.delete(aid)

    async def purge_workspace(self, workspace_id):
        rows = await self.store.rows('''SELECT a.* FROM attachments a JOIN attachment_drafts d USING(draft_id)
            WHERE a.task_id IN (SELECT task_id FROM tasks WHERE workspace_id=?)
            OR (a.task_id IS NULL AND d.workspace_id=?)''', (workspace_id, workspace_id))
        for row in rows:
            await self.delete(row['attachment_id'])
        await self.store.write("UPDATE attachment_drafts SET state='expired',updated_at=? WHERE workspace_id=?", (now(), workspace_id))

    async def expire_drafts(self):
        drafts = await self.store.rows("SELECT * FROM attachment_drafts d WHERE (state='open' AND expires_at<=?) OR (state IN ('expired','submitted') AND EXISTS (SELECT 1 FROM attachments a WHERE a.draft_id=d.draft_id AND a.task_id IS NULL AND a.state!='deleted')) ORDER BY expires_at LIMIT 50", (now(),))
        for draft in drafts:
            async with self.store.transaction() as db:
                rows = await (await db.execute("SELECT * FROM attachments WHERE draft_id=? AND task_id IS NULL AND state!='deleted'", (draft['draft_id'],))).fetchall()
                if any(r['state'] == 'uploading' and owner_alive(r['writer_pid'], r['writer_started']) for r in rows):
                    continue
                # Closing the draft under the upload reservation lock prevents a
                # new writer from starting between the liveness check and unlink.
                await db.execute("UPDATE attachment_drafts SET state=CASE WHEN state='open' THEN 'expired' ELSE state END WHERE draft_id=?", (draft['draft_id'],))
            for row in rows:
                await self.delete(row['attachment_id'])

    async def reconcile(self):
        for candidate in await self.store.rows("SELECT attachment_id FROM attachments WHERE state IN ('uploading','deleting','failed')"):
            async with self.store.transaction() as db:
                row = await one(db, 'SELECT * FROM attachments WHERE attachment_id=?', (candidate['attachment_id'],))
                if row['state'] not in {'uploading', 'deleting', 'failed'}:
                    continue
                if row['state'] == 'uploading' and owner_alive(row['writer_pid'], row['writer_started']):
                    continue
                for path in (self.path(row['storage_key']), self.path(row['storage_key'], True)):
                    path.unlink(missing_ok=True)
                if row['state'] == 'deleting':
                    await db.execute("UPDATE attachments SET state='deleted',deleted_at=?,updated_at=? WHERE attachment_id=?", (now(), now(), row['attachment_id']))
                else:
                    await db.execute("UPDATE attachments SET state='failed',error='Upload interrupted; reselect file',writer_pid=NULL,writer_started=NULL WHERE attachment_id=?", (row['attachment_id'],))
        await self.expire_drafts()

    async def _cleanup(self):
        while True:
            await asyncio.sleep(300)
            try:
                await self.expire_drafts()
            except Exception:
                logger.exception('Attachment cleanup requires retry')

    async def start(self):
        try:
            await self.reconcile()
        except (OSError, RuntimeError):
            logger.exception('Attachment reconciliation incomplete; managed cleanup remains retryable')
        self._cleanup_task = asyncio.create_task(self._cleanup())

    async def stop(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
