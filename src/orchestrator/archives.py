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

from orchestrator.processes import owner_alive, process_group_alive, process_identity
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
                with path.open('rb') as source:
                    if source.read(128).startswith(b'version https://git-lfs.github.com/spec/v1'):
                        raise RuntimeError('LFS pointer capture is unsupported; checkout retained')
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

    async def begin(self, workspace_id):
        async with self.orch.tasks.accept_lock:
            workspace = await self.store.workspace_record(workspace_id)
            if not workspace:
                raise LookupError('Workspace not found')
            if workspace['kind'] != 'worktree':
                raise Conflict('Main folders cannot be deleted')
            if workspace['status'] == 'archived':
                raise Conflict('Workspace is already archived')
            if workspace['status'] == 'archiving':
                previous = await self.store.one('SELECT * FROM archives WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1', (workspace_id,))
                if not previous or owner_alive(previous['owner_pid'], previous['owner_started']):
                    raise Conflict('Deletion belongs to another live or unverifiable service')
            if workspace_id in self.running:
                raise Conflict('Deletion is already running')
            aid = uuid.uuid4().hex
            claimed = await self.store.write("UPDATE workspaces SET status='archiving',updated_at=? WHERE workspace_id=? AND updated_at=?", (now(), workspace_id, workspace['updated_at']))
            if not claimed:
                raise Conflict('Workspace changed while deletion was being requested; retry')
            self.running[workspace_id] = aid
            await self.store.write('INSERT INTO archives (archive_id,workspace_id,stage,created_at,updated_at,owner_pid,owner_started) VALUES (?,?,?,?,?,?,?)', (aid, workspace_id, 'blocking', now(), now(), os.getpid(), process_identity(os.getpid())))
        return workspace, aid

    async def archive_and_delete(self, workspace_id):
        workspace, aid = await self.begin(workspace_id)
        return await self.execute_archive(workspace, aid)

    async def execute_archive(self, workspace, aid):
        workspace_id = workspace['workspace_id']
        try:
            await self.stage(aid, 'stopping')
            lease = await self.store.one('SELECT * FROM workspace_leases WHERE workspace_id=?', (workspace_id,))
            if lease and lease['task_id'] not in self.orch.tasks.running:
                raise Conflict('Workspace has an execution lease owned by another service; checkout retained')
            tasks = await self.store.rows('SELECT task_id,status FROM tasks WHERE workspace_id=?', (workspace_id,))
            agent_ids = set()
            for task in tasks:
                if task['status'] not in {'completed','failed','cancelled'} or task['task_id'] in self.orch.tasks.running:
                    await self.orch.tasks.cancel(task['task_id'])
                for agent in await self.store.rows('SELECT agent_id FROM agents WHERE task_id=?', (task['task_id'],)):
                    agent_ids.add(agent['agent_id'])
            # Legacy sessions can use this checkout without belonging to a task.
            root = Path(workspace['path']).resolve()
            for thread in await self.store.rows('SELECT agent_id,cwd FROM threads WHERE cwd IS NOT NULL'):
                if Path(thread['cwd']).resolve().is_relative_to(root):
                    agent_ids.add(thread['agent_id'])
            for agent in self.orch.registry.agents.values():
                if any(t.cwd and Path(t.cwd).resolve().is_relative_to(root) for t in agent._threads.values()):
                    agent_ids.add(agent.agent_id)
            for agent_id in agent_ids:
                runtime = self.orch.registry.get_agent(agent_id)
                if runtime and any(turn.get('workspace') and not Path(turn['workspace']).resolve().is_relative_to(root) for turn in runtime.activity['threads']):
                    raise Conflict('A legacy agent is active in another workspace; checkout retained')
                if not runtime:
                    record = await self.store.one('SELECT * FROM agents WHERE agent_id=?', (agent_id,))
                    if record and ((record['owner_pid'] and owner_alive(record['owner_pid'], record['owner_started'])) or
                                   (record['pid'] and process_group_alive(record['pid'])) or
                                   record['status'] in {'starting','running','degraded','stopping'}):
                        raise Conflict('A legacy agent is still running in another service; checkout retained')
                await self.orch.registry.destroy_agent(agent_id)
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
                    entries = await _run(str(root), 'git', 'ls-files', '--stage')
                    if (root / '.gitmodules').exists() or any(line.startswith('160000 ') for line in entries.splitlines()):
                        raise Conflict('Submodule archive capture is unsupported; checkout retained')
                    lfs_config = Path(repository['common_dir'], 'lfs')
                    lfs_storage = await _run(str(root), 'git', 'config', '--default', '', '--get', 'lfs.storage')
                    if lfs_storage or lfs_config.exists() or any('filter=lfs' in p.read_text(errors='replace') for p in root.rglob('.gitattributes') if not p.is_symlink()):
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
                    history = {'repository': repository, 'workspace': workspace}
                    history['tasks'] = await self.store.rows('SELECT * FROM tasks WHERE workspace_id=?', (workspace_id,))
                    for table in ('agents','threads','workflow_runs','work_items','step_executions','task_events','input_requests'):
                        history[table] = await self.store.rows(f'SELECT * FROM {table} WHERE task_id IN (SELECT task_id FROM tasks WHERE workspace_id=?)', (workspace_id,))
                    legacy_threads = [thread for thread in await self.store.rows('SELECT * FROM threads WHERE task_id IS NULL AND cwd IS NOT NULL')
                        if Path(thread['cwd']).resolve().is_relative_to(root.resolve())]
                    legacy_ids = {thread['thread_id'] for thread in legacy_threads}
                    history['legacy_threads'] = legacy_threads
                    history['legacy_agents'] = [agent for agent in await self.store.rows('SELECT * FROM agents WHERE task_id IS NULL')
                        if agent['agent_id'] in {thread['agent_id'] for thread in legacy_threads}]
                    history['legacy_events'] = [event for event in await self.store.rows('SELECT * FROM task_events WHERE task_id IS NULL')
                        if event['thread_id'] in legacy_ids]
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
                self.orch.workspace._active.pop(workspace_id, None)
                await self.stage(aid, 'archived')
            return await self.store.one('SELECT * FROM archives WHERE archive_id=?', (aid,))
        except BaseException as exc:
            failed = await self.store.one('SELECT stage FROM archives WHERE archive_id=?', (aid,))
            await self.stage(aid, 'failed', failed['stage'] + ': ' + (str(exc) or 'Deletion interrupted'))
            await self.store.write("UPDATE workspaces SET status='archive_failed',updated_at=? WHERE workspace_id=?", (now(), workspace_id))
            raise
        finally:
            self.running.pop(workspace_id, None)
