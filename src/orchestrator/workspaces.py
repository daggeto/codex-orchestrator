"""Persistent Git discovery and task checkout selection. Never switch a checkout."""
from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

from orchestrator.state.models import now
from orchestrator.workflows.merge_request import _run


class WorkspaceService:
    def __init__(self, orch):
        self.orch = orch
        self.store = orch.store
        self.locks = {}
        self.provision_lock = asyncio.Lock()

    def lock(self, workspace_id):
        return self.locks.setdefault(workspace_id, asyncio.Lock())

    async def register(self, path):
        root = await _run(str(Path(path).expanduser().resolve()), 'git', 'rev-parse', '--show-toplevel')
        common = await _run(root, 'git', 'rev-parse', '--path-format=absolute', '--git-common-dir')
        output = await _run(root, 'git', 'worktree', 'list', '--porcelain')
        entries = []
        for block in output.split('\n\n'):
            fields = dict(line.split(' ', 1) if ' ' in line else (line, '') for line in block.splitlines())
            if fields.get('worktree'):
                entries.append(fields)
        main = str(Path(entries[0]['worktree']).resolve())
        record = await self.store.one('SELECT * FROM repositories WHERE common_dir=?', (common,))
        rid = record['repository_id'] if record else uuid.uuid4().hex
        await self.store.write('INSERT INTO repositories VALUES (?,?,?,?,?,?,?) ON CONFLICT(repository_id) DO UPDATE SET available=excluded.available,updated_at=excluded.updated_at',
                               (rid, Path(main).name, main, common, int(Path(main).is_dir()), now(), now()))
        for entry in entries:
            checkout = str(Path(entry['worktree']).resolve())
            await self.store.write('INSERT INTO workspaces VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET branch=excluded.branch,updated_at=excluded.updated_at',
                                   (uuid.uuid4().hex, rid, 'main' if checkout == main else 'worktree', checkout,
                                    entry.get('branch', '').removeprefix('refs/heads/'), entry.get('HEAD'), 'active', now(), now()))
        return await self.store.one('SELECT * FROM repositories WHERE repository_id=?', (rid,))

    async def reconcile(self):
        for project in await self.store.list_projects():
            try:
                await self.register(project['path'])
            except (OSError, RuntimeError, ValueError):
                await self.store.write('UPDATE repositories SET available=0 WHERE main_path=?', (project['path'],))
        # Only checkout paths actually stored by runs are evidence for ownership.
        for run in await self.store.list_workflow_runs(200):
            variables = {**run.config.get('variables', {}), **run.state.get('variables', {}), **run.state.get('final_variables', {})}
            path = variables.get('task_worktree') or variables.get('cwd')
            if path and Path(path).is_dir():
                try:
                    await self.register(path)
                except (OSError, RuntimeError, ValueError):
                    continue
            # Legacy runs remain readable through their existing API. No agent
            # ownership is inferred from a globally reused role.
        await self.store.write('DELETE FROM workspace_leases')
        await self.store.write("UPDATE tasks SET status='interrupted',phase='Service restarted; continue explicitly',updated_at=? WHERE status IN ('queued','active','waiting_input')", (now(),))
        await self.store.write("UPDATE input_requests SET status='expired',updated_at=? WHERE status='pending'", (now(),))
        await self.store.write("UPDATE step_executions SET status='interrupted',updated_at=? WHERE status='active'", (now(),))
        await self.store.write("UPDATE archives SET stage='interrupted',error='Service restarted; retry deletion after inspecting archive',updated_at=? WHERE stage NOT IN ('archived','failed')", (now(),))

    async def ensure_task_workspace(self, task_id, isolate):
        async with self.provision_lock:
            task = await self.store.task(task_id)
            workspace = await self.store.workspace_record(task['workspace_id'])
            if workspace['status'] != 'active':
                raise RuntimeError('Workspace is unavailable or being archived')
            if not Path(workspace['path']).is_dir():
                raise RuntimeError('Workspace directory is unavailable')
            if workspace['kind'] == 'main' and isolate:
                wt = await self.orch.workspace.create_worktree(workspace['path'], task_id, task_id)
                await self.register(wt.worktree_path)
                workspace = await self.store.one('SELECT * FROM workspaces WHERE path=?', (wt.worktree_path,))
                await self.store.write('UPDATE tasks SET workspace_id=?,updated_at=? WHERE task_id=?', (workspace['workspace_id'], now(), task_id))
            return workspace
