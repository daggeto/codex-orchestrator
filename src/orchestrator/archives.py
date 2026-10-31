"""Verified, recoverable archives must precede any destructive checkout removal."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile
import uuid

from orchestrator.state.models import now
from orchestrator.tasks import Conflict
from orchestrator.workflows.merge_request import _run


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def snapshot(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory, name)
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                result[relative] = {'link': os.readlink(path)}
            elif stat.S_ISREG(metadata.st_mode):
                result[relative] = {'sha256': digest(path), 'size': metadata.st_size}
            elif stat.S_ISDIR(metadata.st_mode):
                result[relative] = {'directory': True}
            else:
                raise RuntimeError(f'Unsupported filesystem entry: {relative}; checkout retained')
    return result


def capture(root, target, bundle, history):
    manifest = snapshot(root)
    manifest_path = target.parent / 'manifest.json'
    manifest_path.write_text(json.dumps({'files': manifest, 'history': history}, indent=2))
    os.chmod(manifest_path, 0o600)
    with tarfile.open(target, 'w:gz', dereference=False) as archive:
        for relative in manifest:
            archive.add(root / relative, arcname='checkout/' + relative, recursive=False)
        archive.add(bundle, arcname='history.bundle')
        archive.add(manifest_path, arcname='manifest.json')
    os.chmod(target, 0o600)
    with tarfile.open(target, 'r:gz') as archive:
        for relative, expected in manifest.items():
            member = archive.getmember('checkout/' + relative)
            if 'sha256' in expected:
                h = hashlib.sha256()
                with archive.extractfile(member) as source:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        h.update(block)
                if h.hexdigest() != expected['sha256']:
                    raise RuntimeError('Archive verification failed: ' + relative)
            elif 'link' in expected and (not member.issym() or member.linkname != expected['link']):
                raise RuntimeError('Symlink verification failed: ' + relative)
        h = hashlib.sha256()
        with archive.extractfile('history.bundle') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != digest(bundle):
            raise RuntimeError('Git bundle capture verification failed')
        with archive.extractfile('manifest.json') as source:
            if json.load(source) != {'files': manifest, 'history': history}:
                raise RuntimeError('History manifest verification failed')
    if snapshot(root) != manifest:
        raise RuntimeError('Checkout changed during capture; retained for retry')
    return digest(target)


class ArchiveService:
    def __init__(self, orch):
        self.orch = orch
        self.store = orch.store
        self.running = {}

    async def stage(self, aid, stage, error=None):
        await self.store.write('UPDATE archives SET stage=?,error=?,updated_at=? WHERE archive_id=?', (stage, error, now(), aid))

    async def archive_and_delete(self, workspace_id):
        async with self.orch.tasks.accept_lock:
            workspace = await self.store.workspace_record(workspace_id)
            if not workspace:
                raise LookupError('Workspace not found')
            if workspace['kind'] != 'worktree':
                raise Conflict('Main folders cannot be deleted')
            if workspace['status'] == 'archived':
                return await self.store.one('SELECT * FROM archives WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1', (workspace_id,))
            if workspace_id in self.running:
                raise Conflict('Deletion is already running')
            aid = uuid.uuid4().hex
            self.running[workspace_id] = aid
            await self.store.write("UPDATE workspaces SET status='archiving',updated_at=? WHERE workspace_id=?", (now(), workspace_id))
            await self.store.write('INSERT INTO archives (archive_id,workspace_id,stage,created_at,updated_at) VALUES (?,?,?,?,?)', (aid, workspace_id, 'blocking', now(), now()))
        try:
            await self.stage(aid, 'stopping')
            for task in await self.store.rows('SELECT task_id,status FROM tasks WHERE workspace_id=?', (workspace_id,)):
                if task['status'] not in {'completed','failed','cancelled'}:
                    await self.orch.tasks.cancel(task['task_id'])
                for agent in await self.store.rows('SELECT agent_id FROM agents WHERE task_id=?', (task['task_id'],)):
                    await self.orch.registry.destroy_agent(agent['agent_id'])
            async with self.orch.workspaces.lock(workspace_id):
                root = Path(workspace['path'])
                repository = await self.store.one('SELECT * FROM repositories WHERE repository_id=?', (workspace['repository_id'],))
                main = repository['main_path']
                if root.exists():
                    actual = await _run(str(root), 'git', 'rev-parse', '--path-format=absolute', '--git-common-dir')
                    if actual != repository['common_dir'] or not (root / '.git').is_file():
                        raise Conflict('Checkout identity changed; deletion blocked')
                    # A bundle cannot capture external submodule/LFS object stores.
                    # Fail closed until a complete capture strategy is available.
                    if (root / '.gitmodules').exists():
                        raise Conflict('Submodule archive capture is unsupported; checkout retained')
                    lfs_config = Path(repository['common_dir'], 'lfs')
                    if lfs_config.exists() or any('filter=lfs' in p.read_text(errors='replace') for p in root.rglob('.gitattributes') if not p.is_symlink()):
                        raise Conflict('Git LFS archive capture is unsupported; checkout retained')
                    await self.stage(aid, 'capturing')
                    directory = self.orch.config.data_dir / 'archives' / aid
                    directory.mkdir(parents=True, mode=0o700)
                    os.chmod(directory, 0o700)
                    if directory.resolve().is_relative_to(root.resolve()):
                        raise Conflict('Archive destination must be outside the checkout')
                    bundle = directory / 'history.bundle'
                    await _run(str(root), 'git', 'bundle', 'create', str(bundle), '--all', 'HEAD')
                    os.chmod(bundle, 0o600)
                    await _run(str(root), 'git', 'bundle', 'verify', str(bundle))
                    history = {}
                    history['tasks'] = await self.store.rows('SELECT * FROM tasks WHERE workspace_id=?', (workspace_id,))
                    for table in ('agents','threads','workflow_runs','work_items','step_executions','task_events','input_requests'):
                        history[table] = await self.store.rows(f'SELECT * FROM {table} WHERE task_id IN (SELECT task_id FROM tasks WHERE workspace_id=?)', (workspace_id,))
                    target = directory / 'workspace.tar.gz'
                    checksum = await asyncio.to_thread(capture, root, target, bundle, history)
                    await self.store.write('UPDATE archives SET path=?,checksum=? WHERE archive_id=?', (str(target), checksum, aid))
                    await self.stage(aid, 'verified')
                    await _run(main, 'git', 'worktree', 'remove', '--force', str(root))
                else:
                    # Recover only from a previously verified operation, never
                    # interpret an absent directory as proof of a good archive.
                    previous = await self.store.one("SELECT * FROM archives WHERE workspace_id=? AND checksum IS NOT NULL ORDER BY created_at DESC LIMIT 1", (workspace_id,))
                    if not previous or not Path(previous['path']).is_file() or digest(previous['path']) != previous['checksum']:
                        raise Conflict('Checkout missing and no verified archive found')
                    await self.store.write('UPDATE archives SET path=?,checksum=? WHERE archive_id=?', (previous['path'], previous['checksum'], aid))
                await self.store.write("UPDATE workspaces SET status='archived',updated_at=? WHERE workspace_id=?", (now(), workspace_id))
                await self.stage(aid, 'archived')
            return await self.store.one('SELECT * FROM archives WHERE archive_id=?', (aid,))
        except BaseException as exc:
            failed = await self.store.one('SELECT stage FROM archives WHERE archive_id=?', (aid,))
            await self.stage(aid, 'failed', failed['stage'] + ': ' + (str(exc) or 'Deletion interrupted'))
            await self.store.write("UPDATE workspaces SET status='archive_failed',updated_at=? WHERE workspace_id=?", (now(), workspace_id))
            raise
        finally:
            self.running.pop(workspace_id, None)
