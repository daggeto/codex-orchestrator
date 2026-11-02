"""Explicit, idempotent recovery cycles; acceptance is never proof of repair."""
from __future__ import annotations

import asyncio
import json
import re

from aiohttp import web

from orchestrator.api.submissions import own_task
from orchestrator.processes import owner_alive
from orchestrator.state.models import now
from orchestrator.tasks import Conflict
from orchestrator.workflows.engine import continuation_checkpoint


async def retry(request):
    return await asyncio.shield(own_task(request.app, accept(request)))


async def accept(request):
    orch = request.app['orch']
    data = await request.json()
    token = data.get('request_id') if isinstance(data, dict) else None
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', token):
        raise ValueError('A request_id of 1-128 letters, digits, underscores or hyphens is required')
    iid = request.match_info['incident_id']
    if 'workspace_id' in request.match_info:
        async with orch.tasks.accept_lock:
            return await retry_merge(request, iid, token)
    tid = request.match_info['task_id']
    async with orch.tasks.accept_lock:
        incident = await orch.store.one('SELECT * FROM workflow_recoveries WHERE incident_id=? AND task_id=?', (iid, tid))
        if not incident or incident['operation_id']:
            raise LookupError('Task recovery incident not found')
        prior = await orch.store.one('SELECT * FROM recovery_retry_requests WHERE request_id=?', (token,))
        if prior:
            if prior['incident_id'] != iid or prior['task_id'] != tid:
                raise Conflict('Request identity already used for a different recovery')
            return web.json_response({'request_id': token, 'task_id': tid}, status=202)
        task = await orch.store.task(tid)
        if (incident['status'] != 'needs_input' or task['archived_at'] or task['kind'] != 'workflow'
            or task['status'] not in {'failed', 'interrupted', 'waiting_input'}
            or tid in orch.tasks.running or tid in orch.tasks.cancelling):
            raise Conflict('Task is busy, closed, archived, or no longer needs recovery')
        if task['owner_pid'] and task['owner_pid'] != orch.tasks.owner_pid and owner_alive(task['owner_pid'], task['owner_started']):
            raise Conflict('Task belongs to another live service')
        latest = await orch.store.one('SELECT run_id FROM workflow_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (tid,))
        if incident['run_id'] and (not latest or latest['run_id'] != incident['run_id']):
            raise Conflict('Incident belongs to an older workflow run')
        run = await orch.store.get_workflow_run(latest['run_id']) if latest else None
        mode = False
        if run:
            definition = orch.workflows.get(run.workflow_name)
            if definition is None or run.status not in {'paused', 'failed', 'interrupted'}:
                raise Conflict('Workflow is unavailable or no longer stopped')
            checkpoint = continuation_checkpoint(definition, run)
            if checkpoint['iteration'] >= run.config.get('iteration_limit', definition.max_iterations):
                raise Conflict('The workflow step budget needs explicit extension through Continue')
            checkpoint['variables']['_assistant_retry_request'] = token
            if run.state.get('phase') == 'waiting_for_plan_approval':
                checkpoint['variables']['_plan_action'] = {'action': 'assess'}
            elif task['status'] == 'waiting_input':
                raise Conflict('Resolve the pending user decision before retrying recovery')
            else:
                mode = 'review' if incident['result'].get('repair_commit') else 'recover'
            run.state.update(checkpoint=checkpoint, phase='accepted')
        async with orch.store.transaction() as db:
            queued = await db.execute("""UPDATE tasks SET status='queued',phase='Recovery accepted',completed_at=NULL,updated_at=?
                WHERE task_id=? AND updated_at=? AND archived_at IS NULL AND status=?
                AND EXISTS (SELECT 1 FROM workspaces w WHERE w.workspace_id=tasks.workspace_id AND w.status='active'
                  AND NOT EXISTS (SELECT 1 FROM workspace_leases l WHERE l.workspace_id=w.workspace_id))""",
                (now(), tid, task['updated_at'], task['status']))
            if queued.rowcount != 1:
                raise Conflict('Task or workspace changed while accepting recovery')
            if run:
                saved = await db.execute("UPDATE workflow_runs SET status='accepted',state_json=?,updated_at=?,completed_at=NULL WHERE run_id=? AND updated_at=?",
                    (json.dumps(run.state), now(), run.run_id, run.updated_at))
                if saved.rowcount != 1:
                    raise Conflict('Workflow changed while accepting recovery')
            await db.execute('INSERT INTO recovery_retry_requests (request_id,incident_id,task_id,created_at,updated_at) VALUES (?,?,?,?,?)',
                (token, iid, tid, now(), now()))
            await db.execute("UPDATE workflow_recoveries SET status='superseded',updated_at=? WHERE incident_id=?", (now(), iid))
        if run:
            run.status = 'accepted'
        orch.tasks.schedule(tid, orch.tasks.execute(tid, continuation=run, assistance_only=mode,
            recovery_request={'request_id': token, 'incident_id': iid}))
        return web.json_response({'request_id': token, 'task_id': tid}, status=202)


async def retry_merge(request, iid, token):
    orch = request.app['orch']
    wid = request.match_info['workspace_id']
    incident = await orch.store.one('SELECT * FROM workflow_recoveries WHERE incident_id=?', (iid,))
    if not incident or not incident['operation_id']:
        raise LookupError('Merge recovery incident not found')
    original = await orch.store.merge_operation(incident['operation_id'])
    if wid != original['source_workspace_id']:
        raise Conflict('Retry the merge from its source workspace')
    prior = await orch.store.one('SELECT * FROM recovery_retry_requests WHERE request_id=?', (token,))
    if prior and (prior['incident_id'] != iid or prior['operation_id'] != token):
        raise Conflict('Request identity already used for a different recovery')
    if not prior:
        if original['state'] != 'failed' or incident['status'] != 'needs_input':
            raise Conflict('Retained merge state needs resolution before a new merge attempt')
    # A retry authorizes only the exact previously captured commits. Changed
    # branches need the ordinary Merge UI to present and accept their new snapshot.
    payload = {key: original[key] for key in ('source_branch', 'source_sha', 'destination_workspace_id', 'destination_path', 'destination_sha')}
    payload['operation_id'] = token
    operation, created = await orch.merges.begin(wid, payload)
    if created:
        own_task(request.app, orch.merges.execute(operation['operation_id']))
    if not prior:
        await orch.store.write('INSERT OR IGNORE INTO recovery_retry_requests (request_id,incident_id,task_id,operation_id,created_at,updated_at) VALUES (?,?,?,?,?,?)',
            (token, iid, incident['task_id'], token, now(), now()))
        await orch.store.write("UPDATE workflow_recoveries SET status='superseded',updated_at=? WHERE incident_id=?", (now(), iid))
        current = await orch.store.merge_operation(operation['operation_id'])
        if current and current['state'] in {'merged', 'already_integrated', 'failed', 'recovery_required'}:
            await orch.store.write("UPDATE recovery_retry_requests SET status='finished',updated_at=? WHERE request_id=?", (now(), token))
    return web.json_response({'request_id': token, 'operation_id': operation['operation_id']}, status=202)
