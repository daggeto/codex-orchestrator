"""Accepted workflow submissions owned by the service, not an HTTP connection."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone

from aiohttp import web

from orchestrator.api.projects import normalize_directory
from orchestrator.state.store import WorkflowRunRecord


def own_task(app, operation):
    task = asyncio.create_task(operation)
    app["submitted_tasks"].add(task)

    def finished(done):
        app["submitted_tasks"].discard(done)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    return task


async def recover_submissions(app):
    # Accepted work survives browser navigation. Service restarts never silently
    # replay potentially destructive steps; retain an explicit interrupted state.
    await interrupt_unfinished(app, "Service restarted; inspect saved progress before submitting a new run")


async def interrupt_unfinished(app, reason):
    store = app["orch"].store
    for record in await store.list_workflow_runs():
        if record.config.get("submission_fingerprint") and record.status in {"accepted", "starting", "running"}:
            record.status = "interrupted"
            record.state.update(phase="interrupted", error=reason)
            await store.save_workflow_run(record)


async def execute(app, workflow, record):
    orch = app["orch"]
    try:
        record.state["phase"] = "starting_team"
        await orch.store.save_workflow_run(record)
        team = await orch.spawn_team(workflow.required_roles)
        record.state["team"] = team
        await orch.store.save_workflow_run(record)
        await orch.engine.run(workflow, variables=record.config["variables"],
                              run_id=record.run_id, accepted=True)
    except (Exception, asyncio.CancelledError) as exc:
        latest = await orch.store.get_workflow_run(record.run_id) or record
        latest.status = "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
        latest.state.update(phase=latest.status, error=str(exc) or "Service stopped during submission")
        latest.completed_at = datetime.now(timezone.utc).isoformat()
        await orch.store.save_workflow_run(latest)
        if isinstance(exc, asyncio.CancelledError):
            raise


async def submit(request: web.Request) -> web.Response:
    # Shield acceptance itself as well as execution: disconnecting while SQLite
    # commits cannot leave an accepted record without a scheduled service task.
    return await asyncio.shield(own_task(request.app, accept(request)))


async def accept(request):
    orch = request.app['orch']
    from orchestrator.api.tasks import body
    data = await body(request)
    variables = data.get('variables')
    rid = data.get('run_id')
    if not isinstance(variables, dict) or not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', rid):
        raise ValueError('Expected run_id and workflow variables')
    path = normalize_directory(variables.get('cwd'))
    await orch.workspaces.register(path)
    workspace = await orch.store.one('SELECT * FROM workspaces WHERE path=?', (path,))
    task = await orch.tasks.submit(workspace['workspace_id'], variables.get('task_description'),
        settings={'workflow': request.match_info['name'], 'variables': variables}, idempotency_key=rid)
    return web.json_response({'run_id': rid, 'task_id': task['task_id'], 'status_url': f'/api/workflows/runs/{rid}'}, status=202)


async def get_run(request):
    record = await request.app["orch"].store.get_workflow_run(request.match_info["run_id"])
    if record is None:
        task = await request.app['orch'].store.one('SELECT * FROM tasks WHERE idempotency_key=?', (request.match_info['run_id'],))
        if task:
            return web.json_response({'run': {'run_id': request.match_info['run_id'], 'workflow_name': task['settings'].get('workflow'),
                'status': task['status'], 'config': task['settings'], 'state': {'phase': task['phase']},
                'created_at': task['created_at'], 'updated_at': task['updated_at'], 'completed_at': task['completed_at']}})
        return web.json_response({"error": "Workflow run not found"}, status=404)
    return web.json_response({"run": asdict(record)})
