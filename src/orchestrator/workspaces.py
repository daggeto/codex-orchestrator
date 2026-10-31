"""Persistent Git discovery and task checkout selection. Never switch a checkout."""
from __future__ import annotations

import asyncio
from pathlib import Path
import uuid
import hashlib

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
        # Paths saved by a run establish task ownership. Role reuse alone never does.
        for run in await self.store.rows('SELECT * FROM workflow_runs WHERE task_id IS NULL'):
            variables = {**run['config'].get('variables', {}), **run['state'].get('variables', {}), **run['state'].get('final_variables', {})}
            path = variables.get('task_worktree') or variables.get('cwd')
            if not path:
                continue
            if Path(path).is_dir():
                try:
                    await self.register(path)
                except (OSError, RuntimeError, ValueError):
                    continue
            workspace = await self.store.one('SELECT * FROM workspaces WHERE path=?', (str(Path(path).resolve()),))
            if not workspace:
                continue
            import json
            tid = 'legacy-' + run['run_id']
            prompt = variables.get('task_description') or run['workflow_name']
            status = run['status'] if run['status'] in {'completed','failed','cancelled','interrupted'} else 'interrupted'
            await self.store.write('INSERT OR IGNORE INTO tasks (task_id,workspace_id,title,prompt,kind,status,phase,settings_json,fingerprint,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (tid, workspace['workspace_id'], prompt[:100], prompt, 'workflow', status, run['state'].get('phase', status),
                 json.dumps({'workflow': run['workflow_name'], 'variables': variables, 'legacy': True}), '', run['created_at'], run['updated_at']))
            await self.store.write('UPDATE workflow_runs SET task_id=? WHERE run_id=?', (tid, run['run_id']))
            await self.store.write('UPDATE work_items SET task_id=? WHERE workflow_id=? AND task_id IS NULL', (tid, run['run_id']))
        evidence = {}
        for run in await self.store.rows('SELECT task_id,state_json FROM workflow_runs WHERE task_id IS NOT NULL'):
            for aid in run['state'].get('team', {}).values():
                evidence.setdefault(aid, set()).add(run['task_id'])
        for aid, owners in evidence.items():
            if len(owners) == 1:
                tid = next(iter(owners))
                await self.store.write('UPDATE agents SET task_id=? WHERE agent_id=? AND task_id IS NULL', (tid, aid))
                await self.store.write('UPDATE threads SET task_id=? WHERE agent_id=? AND task_id IS NULL AND cwd=(SELECT path FROM workspaces JOIN tasks USING(workspace_id) WHERE task_id=?)', (tid, aid, tid))
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
                wt = await self.orch.workspace.create_worktree(workspace['path'], hashlib.sha256(task_id.encode()).hexdigest()[:16], hashlib.sha256(task_id.encode()).hexdigest()[:16])
                await self.register(wt.worktree_path)
                workspace = await self.store.one('SELECT * FROM workspaces WHERE path=?', (wt.worktree_path,))
                await self.store.write('UPDATE tasks SET workspace_id=?,updated_at=? WHERE task_id=?', (workspace['workspace_id'], now(), task_id))
            return workspace
