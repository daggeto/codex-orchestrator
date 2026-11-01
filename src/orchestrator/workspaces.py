"""Persistent Git discovery and task checkout selection. Never switch a checkout."""
from __future__ import annotations

import asyncio
from pathlib import Path
import uuid
import hashlib

from orchestrator.processes import owner_alive, process_group_alive

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

    async def claim_lease(self, workspace_id, task_id, owner_pid, owner_started):
        from orchestrator.tasks import Conflict
        try:
            claimed = await self.store.write(
                "INSERT INTO workspace_leases (workspace_id,task_id,owner_pid,owner_started) "
                "SELECT workspace_id,?,?,? FROM workspaces WHERE workspace_id=? AND status='active'",
                (task_id, owner_pid, owner_started, workspace_id))
        except Exception as exc:
            raise Conflict('Workspace is already executing another task') from exc
        if not claimed:
            raise Conflict('Workspace is merging, awaiting merge recovery, being archived, or unavailable')

    async def backends_stopped(self, lease):
        workspace = await self.store.workspace_record(lease['workspace_id'])
        root = Path(workspace['path']).resolve()
        task = await self.store.task(lease['task_id'])
        ids = {task['settings'].get('legacy_agent_id')} if task else set()
        ids.update(agent['agent_id'] for agent in await self.store.task_agents(lease['task_id']))
        for thread in await self.store.rows('SELECT agent_id,cwd FROM threads WHERE cwd IS NOT NULL'):
            if Path(thread['cwd']).resolve().is_relative_to(root):
                ids.add(thread['agent_id'])
        for agent in await self.store.rows('SELECT * FROM agents'):
            if agent['task_id'] != lease['task_id'] and agent['agent_id'] not in ids:
                continue
            if agent['owner_pid'] and owner_alive(agent['owner_pid'], agent['owner_started']):
                return False
            if agent['pid']:
                if process_group_alive(agent['pid']):
                    return False
            elif agent['owner_pid'] or agent['status'] in {'starting','running','degraded','stopping','error'}:
                # A dead service owner does not prove a backend without a saved
                # process-group identity has stopped.
                return False
        return True

    async def register(self, path):
        root = await _run(str(Path(path).expanduser().resolve()), 'git', 'rev-parse', '--show-toplevel')
        common = await _run(root, 'git', 'rev-parse', '--path-format=absolute', '--git-common-dir')
        output = await _run(root, 'git', 'worktree', 'list', '--porcelain', '-z')
        entries, fields = [], {}
        for field in output.split('\0'):
            if not field:
                if fields.get('worktree'):
                    entries.append(fields)
                fields = {}
            else:
                key, _, value = field.partition(' ')
                fields[key] = value
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
            tid = run['created_at'] + '-legacy-' + run['run_id']
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
        from orchestrator.workspace import Worktree
        for workspace in await self.store.rows("SELECT w.*,r.main_path FROM workspaces w JOIN repositories r USING(repository_id) WHERE w.kind='worktree' AND w.status!='archived'"):
            if Path(workspace['path']).is_dir():
                self.orch.workspace._active[workspace['workspace_id']] = Worktree(
                    worktree_id=workspace['workspace_id'], repo_path=workspace['main_path'],
                    worktree_path=workspace['path'], branch=workspace['branch'] or '',
                    base_branch=workspace['base_commit'] or 'HEAD')
        for lease in await self.store.rows('SELECT * FROM workspace_leases'):
            if not owner_alive(lease['owner_pid'], lease['owner_started']) and await self.backends_stopped(lease):
                await self.store.write('DELETE FROM workspace_leases WHERE workspace_id=? AND owner_pid=? AND owner_started=?',
                                       (lease['workspace_id'], lease['owner_pid'], lease['owner_started']))
        for task in await self.store.rows("SELECT * FROM tasks WHERE status IN ('queued','active','waiting_input')"):
            lease = await self.store.one('SELECT * FROM workspace_leases WHERE task_id=?', (task['task_id'],))
            if lease or (task['owner_pid'] and owner_alive(task['owner_pid'], task['owner_started'])):
                continue
            run = await self.store.one('SELECT * FROM workflow_runs WHERE task_id=? ORDER BY updated_at DESC LIMIT 1', (task['task_id'],))
            if run and run['status'] in {'completed', 'failed', 'cancelled', 'interrupted', 'paused'}:
                status = 'interrupted' if run['status'] == 'paused' else run['status']
                phase = run['state'].get('error') or run['state'].get('phase') or status
                await self.store.task_status(task['task_id'], status, phase)
            else:
                await self.store.task_status(task['task_id'], 'interrupted', 'Owner stopped; continue explicitly')
            await self.store.write("UPDATE input_requests SET status='expired',updated_at=? WHERE task_id=? AND status='pending'", (now(), task['task_id']))
            await self.store.write("UPDATE step_executions SET status='interrupted',updated_at=? WHERE task_id=? AND status='active'", (now(), task['task_id']))
        for archive in await self.store.rows("SELECT * FROM archives WHERE stage NOT IN ('archived','failed','interrupted')"):
            if not owner_alive(archive['owner_pid'], archive['owner_started']):
                await self.store.write("UPDATE archives SET stage='interrupted',error='Owner stopped; inspect and retry deletion',updated_at=? WHERE archive_id=?", (now(), archive['archive_id']))
                await self.store.write("UPDATE workspaces SET status='archive_failed',updated_at=? WHERE workspace_id=? AND status='archiving'", (now(), archive['workspace_id']))
        for agent in await self.store.rows("SELECT agent_id,pid,owner_pid,owner_started FROM agents WHERE status IN ('running','starting','stopping','degraded')"):
            if agent['owner_pid'] and owner_alive(agent['owner_pid'], agent['owner_started']):
                continue
            # No backend PID (legacy/Claude) cannot prove descendants stopped.
            if agent['pid'] and not process_group_alive(agent['pid']):
                await self.store.update_agent_status(agent['agent_id'], 'stopped', pid=None, ws_url=None)

    async def ensure_task_workspace(self, task_id, isolate):
        async with self.provision_lock:
            task = await self.store.task(task_id)
            workspace = await self.store.workspace_record(task['workspace_id'])
            if workspace['status'] != 'active':
                raise RuntimeError('Workspace is merging, awaiting merge recovery, being archived, or unavailable')
            if not Path(workspace['path']).is_dir():
                raise RuntimeError('Workspace directory is unavailable')
            if workspace['kind'] == 'main' and isolate:
                wt = await self.orch.workspace.create_worktree(workspace['path'], hashlib.sha256(task_id.encode()).hexdigest()[:16], hashlib.sha256(task_id.encode()).hexdigest()[:16])
                workspace = await self.store.one('SELECT * FROM workspaces WHERE path=?', (wt.worktree_path,))
                await self.store.write('UPDATE tasks SET workspace_id=?,updated_at=? WHERE task_id=?', (workspace['workspace_id'], now(), task_id))
            return workspace
