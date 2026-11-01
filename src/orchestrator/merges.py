"""Persisted local merges. Never switch branches, reset, fetch, or publish."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from orchestrator.processes import owner_alive, process_group_alive, process_identity, stop_process_group
from orchestrator.state.models import now
from orchestrator.tasks import Conflict

UNRESOLVED = {'accepted', 'running', 'recovery_required'}
IDENTITY = ('source_branch', 'source_sha', 'destination_workspace_id', 'destination_path', 'destination_sha')


class MergeBlocked(Conflict):
    def __init__(self, message, eligibility):
        super().__init__(message)
        self.details = eligibility


class MergeService:
    def __init__(self, orch):
        self.orch = orch
        self.store = orch.store
        self.running = set()
        self.owner_pid = os.getpid()
        self.owner_started = process_identity(self.owner_pid)

    async def git(self, path, *args, operation_id=None, timeout=15):
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_EDITOR': 'true', 'GIT_MERGE_AUTOEDIT': 'no'}
        command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'merge.autoStash=false',
                   '-c', 'commit.gpgSign=false', '-c', 'rerere.enabled=false',
                   '-c', 'branch.main.mergeOptions=', *args]
        if operation_id:
            # The child cannot mutate Git until its identity is durable. A service
            # crash before that closes stdin and exits the gate without exec.
            gate = ('import os,sys; '
                    'token=sys.stdin.buffer.read(1); '
                    'sys.exit(1) if token != b"1" else os.execvpe(sys.argv[1],sys.argv[1:],os.environ)')
            command = [sys.executable, '-c', gate, *command]
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=path, env=env, start_new_session=True,
            stdin=asyncio.subprocess.PIPE if operation_id else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        # Drain both streams, retaining bounded diagnostics even for a noisy driver.
        buffers = [b'', b'']
        async def read(stream, index):
            while chunk := await stream.read(8192):
                buffers[index] = (buffers[index] + chunk)[-65536:]
            return buffers[index].decode(errors='replace')
        readers = [asyncio.create_task(read(proc.stdout, 0)), asyncio.create_task(read(proc.stderr, 1))]
        try:
            if operation_id:
                await asyncio.shield(self.store.write('UPDATE workspace_merges SET git_pid=?,git_started=?,updated_at=? WHERE operation_id=?',
                                       (proc.pid, process_identity(proc.pid), now(), operation_id)))
                proc.stdin.write(b'1')
                await proc.stdin.drain()
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout)
            out, err = await asyncio.wait_for(asyncio.gather(*readers), 5)
            if operation_id and process_group_alive(proc.pid):
                await stop_process_group(proc)
            return proc.returncode, out, err
        except BaseException as exc:
            try:
                await stop_process_group(proc)
            except Exception as cleanup:
                cleanup.diagnostics = b'\n'.join(buffers).decode(errors='replace')[-16000:]
                raise cleanup from exc
            exc.diagnostics = b'\n'.join(buffers).decode(errors='replace')[-16000:]
            raise
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    async def value(self, path, *args):
        code, out, err = await self.git(path, *args)
        if code:
            raise Conflict(f'Git check failed at {path}: {err.strip()[:1000] or out.strip()[:1000]}')
        if len(out.encode()) >= 65536:
            raise Conflict(f'Git inspection output exceeds the safe limit at {path}; merge manually')
        return out.strip()

    async def checkout(self, workspace, repository, branch):
        path = workspace['path']
        root = await self.value(path, 'rev-parse', '--show-toplevel')
        common = await self.value(path, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if Path(root).resolve() != Path(path).resolve() or Path(common).resolve() != Path(repository['common_dir']).resolve():
            raise Conflict(f'Checkout identity changed at {path}')
        actual = await self.value(path, 'symbolic-ref', '--quiet', 'HEAD')
        if actual != 'refs/heads/' + branch:
            raise Conflict(f'Expected branch {branch} at {path}; checkout is detached or changed')
        return await self.value(path, 'rev-parse', 'HEAD')

    async def clean(self, path):
        if await self.value(path, 'status', '--porcelain', '--untracked-files=all'):
            raise Conflict(f'Commit or remove tracked and untracked changes at {path}')
        for name in ('MERGE_HEAD', 'MERGE_AUTOSTASH', 'BISECT_START', 'AUTO_MERGE', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer', 'index.lock'):
            target = await self.value(path, 'rev-parse', '--path-format=absolute', '--git-path', name)
            if Path(target).exists():
                raise Conflict(f'Unfinished Git operation ({name}) at {path}')
        entries = await self.value(path, 'ls-files', '--stage')
        if any(line.startswith('160000 ') for line in entries.splitlines()):
            raise Conflict(f'Submodule merges require manual handling at {path}')

    async def idle(self, workspace):
        wid = workspace['workspace_id']
        if await self.store.one('SELECT 1 FROM workspace_leases WHERE workspace_id=?', (wid,)):
            raise Conflict(f'Workspace is executing a task: {workspace["path"]}')
        # Legacy agents may predate durable leases; fail closed for a live backend.
        root = Path(workspace['path']).resolve()
        for agent in await self.store.rows('SELECT DISTINCT a.* FROM agents a JOIN threads t USING(agent_id) WHERE t.cwd IS NOT NULL AND a.task_id IS NULL'):
            threads = await self.store.rows('SELECT cwd FROM threads WHERE agent_id=? AND cwd IS NOT NULL', (agent['agent_id'],))
            if not any(Path(t['cwd']).resolve().is_relative_to(root) for t in threads):
                continue
            if agent['status'] in {'starting', 'running', 'degraded', 'stopping', 'error'} or (agent['pid'] and process_group_alive(agent['pid'])):
                raise Conflict(f'A legacy agent may still be executing at {workspace["path"]}')
        for agent in self.orch.registry.agents.values():
            if any(t.get('workspace') and Path(t['workspace']).resolve().is_relative_to(root) for t in agent.activity['threads']):
                raise Conflict(f'An agent is executing at {workspace["path"]}')

    async def preflight(self, workspace_id, reserved=False):
        source = await self.store.workspace_record(workspace_id)
        if not source:
            raise LookupError('Workspace not found')
        repository = await self.store.one('SELECT * FROM repositories WHERE repository_id=?', (source['repository_id'],))
        latest = await self.store.one('SELECT * FROM workspace_merges WHERE source_workspace_id=? OR destination_workspace_id=? ORDER BY created_at DESC LIMIT 1', (workspace_id, workspace_id))
        result = dict(eligible=False, blockers=[], source_workspace_id=workspace_id,
                      source_branch=source['branch'], source_sha=None,
                      destination_workspace_id=None, destination_path=None, destination_sha=None,
                      latest_operation=latest)
        try:
            if source['kind'] != 'worktree' or not source['branch'] or source['branch'] == 'main':
                raise Conflict('Select a worktree branch other than main')
            result['source_sha'] = await self.checkout(source, repository, source['branch'])
            # -z avoids quoted worktree paths (spaces, newlines and non-ASCII).
            code, listing, error = await self.git(source['path'], 'worktree', 'list', '--porcelain', '-z')
            if code or len(listing.encode()) >= 65536:
                raise Conflict('Cannot safely enumerate Git worktrees: ' + error[:500])
            entries, entry = [], {}
            for field in listing.split('\0'):
                if not field:
                    if entry:
                        entries.append(entry)
                        entry = {}
                else:
                    key, _, value = field.partition(' ')
                    entry[key] = value
            if entry:
                entries.append(entry)
            destinations = [e for e in entries if e.get('branch') == 'refs/heads/main' and 'prunable' not in e and 'bare' not in e]
            if len(destinations) != 1:
                raise Conflict('Local main must be checked out in exactly one existing checkout; no branches will be switched')
            path = str(Path(destinations[0]['worktree']).resolve())
            result['destination_path'] = path
            destination = await self.store.one('SELECT * FROM workspaces WHERE path=? AND repository_id=?', (path, repository['repository_id']))
            if not destination:
                # Discovery records newly created checkouts without relying on the cache.
                await self.orch.workspaces.register(source['path'])
                destination = await self.store.one('SELECT * FROM workspaces WHERE path=? AND repository_id=?', (path, repository['repository_id']))
            if not destination:
                raise Conflict('Destination checkout has no persisted workspace identity')
            result['destination_workspace_id'] = destination['workspace_id']
            result['destination_sha'] = await self.checkout(destination, repository, 'main')
            for workspace in (source, destination):
                allowed = {'merging'} if reserved else {'active'}
                if workspace['status'] not in allowed:
                    raise Conflict(f'Workspace status is {workspace["status"]}: {workspace["path"]}')
                await self.idle(workspace)
                await self.clean(workspace['path'])
            pending = await self.store.one("SELECT operation_id FROM workspace_merges WHERE repository_id=? AND state IN ('accepted','running','recovery_required')", (repository['repository_id'],))
            if pending and not reserved:
                raise Conflict('Repository has an unresolved merge: ' + pending['operation_id'])
            result['eligible'] = True
        except (Conflict, OSError, TimeoutError) as exc:
            result['blockers'].append(str(exc) or 'Git inspection timed out')
        return result

    def fingerprint(self, workspace_id, request):
        return hashlib.sha256(json.dumps([workspace_id, {k: request[k] for k in IDENTITY}], sort_keys=True).encode()).hexdigest()

    async def begin(self, workspace_id, request):
        if set(request) != {'operation_id', *IDENTITY}:
            raise ValueError('Expected operation_id and source/destination identities and SHAs; only normal merges into local main are supported')
        if any(not isinstance(v, str) or not v for v in request.values()):
            raise ValueError('Merge request fields must be nonempty strings')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request['operation_id']):
            raise ValueError('Invalid operation ID')
        fingerprint = self.fingerprint(workspace_id, request)
        previous = await self.store.merge_operation(request['operation_id'])
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise Conflict('Operation ID belongs to a different merge request')
            return previous, False
        async with self.orch.tasks.accept_lock:
            async with AsyncExitStack() as stack:
                for wid in sorted({workspace_id, request['destination_workspace_id']}):
                    if self.orch.workspaces.lock(wid).locked():
                        previous = await self.store.merge_operation(request['operation_id'])
                        if previous and previous['fingerprint'] == fingerprint:
                            return previous, False
                        raise Conflict('Workspace is busy executing a task or merge')
                    await stack.enter_async_context(self.orch.workspaces.lock(wid))
                previous = await self.store.merge_operation(request['operation_id'])
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise Conflict('Operation ID belongs to a different merge request')
                    return previous, False
                eligibility = await self.preflight(workspace_id)
                if not eligibility['eligible']:
                    # Another process may have accepted this same request during Git inspection.
                    previous = await self.store.merge_operation(request['operation_id'])
                    if previous and previous['fingerprint'] == fingerprint:
                        return previous, False
                    raise MergeBlocked('; '.join(eligibility['blockers']), eligibility)
                if any(eligibility[k] != request[k] for k in IDENTITY):
                    raise MergeBlocked('Source or local main changed; refresh eligibility before merging', eligibility)
                source = await self.store.workspace_record(workspace_id)
                operation = {**request, 'repository_id': source['repository_id'], 'source_workspace_id': workspace_id,
                             'fingerprint': fingerprint, 'state': 'accepted', 'owner_pid': self.owner_pid,
                             'owner_started': self.owner_started, 'created_at': now(), 'updated_at': now()}
                created = await self.store.reserve_merge(operation)
        return await self.store.merge_operation(request['operation_id']), created

    async def destination(self, operation):
        workspace = await self.store.workspace_record(operation['destination_workspace_id'])
        repository = await self.store.one('SELECT * FROM repositories WHERE repository_id=?', (operation['repository_id'],))
        if not workspace or workspace['path'] != operation['destination_path'] or workspace['repository_id'] != operation['repository_id']:
            raise Conflict('Destination workspace identity changed')
        return await self.checkout(workspace, repository, 'main')

    async def ancestor(self, path, ancestor, commit):
        code, _, err = await self.git(path, 'merge-base', '--is-ancestor', ancestor, commit)
        if code not in (0, 1):
            raise Conflict('Cannot inspect merge ancestry: ' + err[:500])
        return code == 0

    async def verified_result(self, operation):
        path = operation['destination_path']
        head = await self.destination(operation)
        await self.clean(path)
        if head == operation['destination_sha']:
            if await self.ancestor(path, operation['source_sha'], head):
                return 'already_integrated', head
            return 'failed', head
        if head == operation['source_sha'] and await self.ancestor(path, operation['destination_sha'], head):
            return 'merged', head
        parents = (await self.value(path, 'show', '-s', '--format=%P', head)).split()
        if parents == [operation['destination_sha'], operation['source_sha']]:
            return 'merged', head
        raise Conflict('Destination HEAD does not match this operation’s captured merge')

    async def recovery(self, operation, message, diagnostics='', conflicts=None):
        guidance = (f'{message}. Inspect {operation["destination_path"]} with git status, git log, and git reflog; '
                    'stop any remaining merge process and resolve or abort unfinished Git operations, then reload to reconcile. No reset was performed.')
        await self.store.finish_merge(operation['operation_id'], 'recovery_required', error_code='recovery_required',
                                      error_message=guidance, diagnostics=diagnostics, conflict_paths=conflicts)

    async def execute(self, operation_id):
        if operation_id in self.running:
            return
        self.running.add(operation_id)
        operation = await self.store.merge_operation(operation_id)
        diagnostics, conflicts = '', []
        merge_started = False
        try:
            async with AsyncExitStack() as stack:
                for wid in sorted({operation['source_workspace_id'], operation['destination_workspace_id']}):
                    await stack.enter_async_context(self.orch.workspaces.lock(wid))
                if operation['state'] != 'accepted':
                    return
                check = await self.preflight(operation['source_workspace_id'], reserved=True)
                if not check['eligible'] or any(check[k] != operation[k] for k in IDENTITY):
                    raise Conflict('; '.join(check['blockers']) or 'Source or destination changed before execution')
                path = operation['destination_path']
                if await self.ancestor(path, operation['source_sha'], operation['destination_sha']):
                    state, head = await self.verified_result(operation)
                    if state != 'already_integrated':
                        raise Conflict('Destination changed during ancestry inspection')
                    await self.store.finish_merge(operation_id, state, head)
                    return
                await asyncio.shield(self.store.write("UPDATE workspace_merges SET state='running',updated_at=? WHERE operation_id=?", (now(), operation_id)))
                merge_started = True
                code, out, err = await self.git(path, 'merge', '--no-edit', '--no-autostash', '--no-overwrite-ignore',
                                                '--no-squash', '--ff', '--commit', '--no-gpg-sign', operation['source_sha'],
                                                operation_id=operation_id, timeout=120)
                diagnostics = (out + '\n' + err)[-16000:]
                if code:
                    _, names, _ = await self.git(path, 'diff', '--name-only', '--diff-filter=U', '-z')
                    conflicts = [name for name in names.split('\0') if name][:200]
                    merge_head = Path(await self.value(path, 'rev-parse', '--path-format=absolute', '--git-path', 'MERGE_HEAD'))
                    # Abort only a merge matching the captured commit and original HEAD.
                    if merge_head.exists():
                        if merge_head.read_text().strip() != operation['source_sha'] or await self.destination(operation) != operation['destination_sha']:
                            raise Conflict('Unfinished merge ownership cannot be established')
                        abort_code, abort_out, abort_err = await self.git(path, 'merge', '--abort', operation_id=operation_id)
                        diagnostics += '\n' + abort_out + abort_err
                        if abort_code:
                            raise Conflict('Merge abort failed')
                    state, head = await self.verified_result(operation)
                    if state != 'failed':
                        raise Conflict('Git reported failure after changing destination HEAD')
                    await self.store.finish_merge(operation_id, 'failed', head, 'merge_conflict' if conflicts else 'git_failed',
                                                  'Merge conflicted and was aborted; resolve the branch and retry.' if conflicts else 'Git merge failed; destination is clean. Inspect diagnostics before retrying.',
                                                  diagnostics, conflicts)
                else:
                    state, head = await self.verified_result(operation)
                    if state not in {'merged', 'already_integrated'}:
                        raise Conflict('Git did not integrate the captured commit')
                    await self.store.finish_merge(operation_id, state, head, diagnostics=diagnostics)
        except BaseException as exc:
            diagnostics = (diagnostics + '\n' + getattr(exc, 'diagnostics', ''))[-16000:]
            message = str(exc) or 'Merge interrupted or timed out'
            if merge_started:
                # Cancellation/timeout cannot assume the subprocess had not committed.
                await self.recovery(operation, message, diagnostics, conflicts)
            else:
                await self.store.finish_merge(operation_id, 'failed', error_code='stale_or_unsafe', error_message=message)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self.running.discard(operation_id)

    async def reconcile(self):
        for operation in await self.store.rows("SELECT * FROM workspace_merges WHERE state IN ('accepted','running','recovery_required')"):
            if operation['operation_id'] in self.running:
                continue
            if operation['state'] != 'recovery_required' and owner_alive(operation['owner_pid'], operation['owner_started']):
                continue
            # Never kill an orphan by an unverified/recycled PID. Retain its reservation.
            if operation['git_pid'] and process_group_alive(operation['git_pid']):
                await self.recovery(operation, 'Owned Git process group may still be running')
                continue
            if operation['state'] == 'accepted':
                await self.store.finish_merge(operation['operation_id'], 'failed', error_code='interrupted', error_message='Service stopped before merge execution; retry with a new operation ID')
                continue
            try:
                state, head = await self.verified_result(operation)
                await self.store.finish_merge(operation['operation_id'], state, head,
                                              error_code='interrupted' if state == 'failed' else None,
                                              error_message='Merge stopped without integrating changes; destination is clean' if state == 'failed' else None,
                                              diagnostics=operation['diagnostics'] or '', conflict_paths=operation['conflict_paths'])
            except (Conflict, OSError, TimeoutError) as exc:
                await self.recovery(operation, str(exc), operation['diagnostics'] or '', operation['conflict_paths'])
