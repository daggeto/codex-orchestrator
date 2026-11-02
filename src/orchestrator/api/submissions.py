"""Accepted workflow submissions owned by the service, not an HTTP connection."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

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
        if (record.config.get("submission_fingerprint") or record.state.get("continuations")) and record.status in {"accepted", "starting", "running"}:
            record.status = "interrupted"
            record.state.update(phase="interrupted", error=reason)
            await store.save_workflow_run(record)


async def execute(app, workflow, record, *, continuing=False):
    orch = app["orch"]
    try:
        record.state["phase"] = "starting_team"
        await orch.store.save_workflow_run(record)
        team = await orch.spawn_team(workflow.required_roles)
        record.state["team"] = team
        await orch.store.save_workflow_run(record)
        await orch.engine.run(workflow, variables=record.config["variables"],
                              run_id=record.run_id, accepted=True, continuing=continuing)
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


async def restart_workspace(orch, task_id):
    source = await orch.store.task(task_id)
    workspace = await orch.store.workspace_record(source['workspace_id']) if source else None
    if workspace and workspace['status'] == 'active' and Path(workspace['path']).is_dir():
        return workspace
    if workspace:
        main = await orch.store.one(
            "SELECT * FROM workspaces WHERE repository_id=? AND kind='main' AND status='active'",
            (workspace['repository_id'],))
        if main and Path(main['path']).is_dir():
            return main
    return None


async def accept(request):
    orch = request.app['orch']
    from orchestrator.api.tasks import body
    data = await body(request)
    variables = data.get('variables')
    rid = data.get('run_id')
    if not isinstance(variables, dict) or not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', rid):
        raise ValueError('Expected run_id and workflow variables')
    previous = await orch.store.one('SELECT * FROM tasks WHERE idempotency_key=?', (rid,))
    if previous:
        # Acceptance recovery must precede directory existence and upload checks.
        # The original checkout may have been removed since the response was lost.
        supplied = variables.get('cwd')
        if not isinstance(supplied, str):
            raise ValueError('Expected a saved workflow directory')
        # Directory lookup canonicalizes paths at acceptance, but the fingerprint
        # retains the supplied spelling. Preserve it on retry as well, including
        # trailing slashes and tilde paths used by existing submissions.
        variables = dict(variables)
        if data.get('copy_from_task_id'):
            source = await orch.store.task(data['copy_from_task_id'])
            source_workspace = await orch.store.workspace_record(source['workspace_id']) if source else None
            if source_workspace and variables['cwd'] == source_workspace['path']:
                variables['cwd'] = previous['settings'].get('variables', {}).get('cwd')
        # Execution may have moved the task into an isolated checkout. Its
        # original fingerprint belongs to the workspace used at acceptance.
        submission_workspace_id = previous.get('submission_workspace_id')
        if not submission_workspace_id:
            # Older records may not have been backfilled when their saved cwd
            # used a noncanonical spelling. Resolve only for workspace lookup,
            # without requiring the original checkout to still exist.
            saved_cwd = previous['settings'].get('variables', {}).get('cwd')
            if isinstance(saved_cwd, str):
                original = await orch.store.one('SELECT workspace_id FROM workspaces WHERE path=?',
                    (normalize_directory(saved_cwd, validate=False),))
                submission_workspace_id = original['workspace_id'] if original else None
        if not submission_workspace_id:
            raise ValueError('The original submission workspace is unavailable for this older task. Open the accepted task from task history.')
        task = await orch.tasks.submit(submission_workspace_id, variables.get('task_description'),
            settings={'workflow': request.match_info['name'], 'variables': variables}, idempotency_key=rid,
            attachment_ids=data.get('attachment_ids'), draft_id=data.get('draft_id'),
            copy_from_task_id=data.get('copy_from_task_id'), omit_unavailable=data.get('omit_unavailable_attachments') is True)
        return web.json_response({'run_id': rid, 'task_id': task['task_id'], 'status_url': f'/api/workflows/runs/{rid}'}, status=202)
    if data.get('copy_from_task_id'):
        source = await orch.store.task(data['copy_from_task_id'])
        source_workspace = await orch.store.workspace_record(source['workspace_id']) if source else None
        destination = await restart_workspace(orch, data['copy_from_task_id'])
        if not destination:
            raise ValueError('No active restart workspace is available. Restore the repository Main workspace and retry.')
        # Recover older saved requests that still name the removed checkout.
        # Only the source checkout may be redirected; arbitrary paths still
        # undergo normal directory and repository validation below.
        if variables.get('cwd') == source_workspace['path']:
            variables = {**variables, 'cwd': destination['path']}
    path = normalize_directory(variables.get('cwd'))
    await orch.workspaces.register(path)
    workspace = await orch.store.one('SELECT * FROM workspaces WHERE path=?', (path,))
    if data.get('copy_from_task_id'):
        source = await orch.store.task(data['copy_from_task_id'])
        source_workspace = await orch.store.workspace_record(source['workspace_id']) if source else None
        if not source_workspace or source_workspace['repository_id'] != workspace['repository_id']:
            raise ValueError('Restart attachments must belong to this repository')
    task = await orch.tasks.submit(workspace['workspace_id'], variables.get('task_description'),
        settings={'workflow': request.match_info['name'], 'variables': variables}, idempotency_key=rid,
        attachment_ids=data.get('attachment_ids'), draft_id=data.get('draft_id'),
        copy_from_task_id=data.get('copy_from_task_id'), omit_unavailable=data.get('omit_unavailable_attachments') is True)
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
    workflow = request.app["orch"].workflows.get(record.workflow_name)
    checkpoint = record.state.get("checkpoint", {})
    reason = None
    if record.status not in {"failed", "interrupted", "cancelled", "paused"}:
        reason = "Only stopped runs can continue"
    elif workflow is None:
        reason = "Workflow definition unavailable"
    elif not await request.app['orch'].store.one('SELECT task_id FROM workflow_runs WHERE run_id=? AND task_id IS NOT NULL', (record.run_id,)):
        reason = "This legacy workflow has no owning task; start a new workspace task"
    else:
        from orchestrator.workflows.engine import continuation_checkpoint
        try:
            checkpoint = continuation_checkpoint(workflow, record)
        except ValueError as exc:
            reason = str(exc)
    iteration_limit = record.config.get("iteration_limit", workflow.max_iterations if workflow else 20)
    requires_extension = (checkpoint.get("next_step") != "__end__"
                          and checkpoint.get("iteration", 0) >= iteration_limit)
    task = await request.app['orch'].store.one('SELECT task_id FROM workflow_runs WHERE run_id=?', (record.run_id,))
    from orchestrator.api.attachments import public
    attachment_rows = await request.app['orch'].store.rows('SELECT * FROM attachments WHERE task_id=? ORDER BY created_at', (task['task_id'],)) if task and task['task_id'] else []
    destination = await restart_workspace(request.app['orch'], task['task_id']) if task and task['task_id'] else None
    return web.json_response({"run": asdict(record), 'task_id': task['task_id'] if task else None,
                             'restart_workspace': {'path': destination['path'], 'kind': destination['kind']} if destination else None,
                             'attachments': [public(row) for row in attachment_rows], "continuation": {
        "available": reason is None, "reason": reason,
        "requires_extension": requires_extension,
        "iteration_limit": iteration_limit, "next_iteration_limit": iteration_limit + 20,
        "step": checkpoint.get("next_step", record.state.get("current_step")),
        "review_refresh_required": bool(workflow and workflow.review_policy
                                       and record.config.get("review_policy") != workflow.review_policy
                                       and reason is None),
    }})


async def continue_run(request):
    return await asyncio.shield(own_task(request.app, accept_continuation(request)))


async def accept_continuation(request):
    async with request.app['orch'].tasks.accept_lock:
        return await _accept_continuation(request)


async def _accept_continuation(request):
    orch = request.app["orch"]
    try:
        body = await request.json()
        token = body.get("request_id") if isinstance(body, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", token):
            raise ValueError("request_id must contain 1-64 letters, digits, underscores or hyphens")
        record = await orch.store.get_workflow_run(request.match_info["run_id"])
        if record is None:
            return web.json_response({"error": "Workflow run not found"}, status=404)
        attempts = record.state.get("continuations", [])
        if any(attempt["request_id"] == token for attempt in attempts):
            return web.json_response({"run_id": record.run_id}, status=202)
        if record.status not in {"failed", "interrupted", "cancelled", "paused"}:
            return web.json_response({"error": "This workflow is already active or completed"}, status=409)
        workflow = orch.workflows.get(record.workflow_name)
        if workflow is None:
            raise ValueError("Workflow definition unavailable")
        from orchestrator.workflows.engine import continuation_checkpoint
        checkpoint = continuation_checkpoint(workflow, record)
        task = await orch.store.one('SELECT * FROM tasks WHERE task_id=(SELECT task_id FROM workflow_runs WHERE run_id=?)', (record.run_id,))
        if not task:
            return web.json_response({'error': 'This legacy workflow has no owning task; start a new workspace task'}, status=409)
        if task:
            from orchestrator.processes import owner_alive
            if task['task_id'] in orch.tasks.running:
                return web.json_response({'error': 'Task execution is still stopping; retry shortly'}, status=409)
            if task['owner_pid'] and task['owner_pid'] != orch.tasks.owner_pid and owner_alive(task['owner_pid'], task['owner_started']):
                return web.json_response({'error': 'Task belongs to another live service'}, status=409)
            workspace = await orch.store.workspace_record(task['workspace_id'])
            if workspace and workspace['status'] in {'merging', 'merge_recovery_required'}:
                return web.json_response({'error': 'Workspace is reserved for merging or merge recovery; continue after it is released'}, status=409)
            if not workspace or workspace['status'] != 'active' or task['archived_at']:
                return web.json_response({'error': 'Task workspace or archived task is unavailable for continuation'}, status=409)
            if await orch.store.one('SELECT 1 FROM workspace_leases WHERE workspace_id=?', (workspace['workspace_id'],)):
                return web.json_response({'error': 'Workspace is already executing another task'}, status=409)
        iteration_limit = record.config.get("iteration_limit", workflow.max_iterations)
        additional_steps = body.get("additional_steps", 0)
        if type(additional_steps) is not int or additional_steps not in (0, 20):
            raise ValueError("additional_steps must be 0 or 20")
        exhausted = (checkpoint["next_step"] != "__end__"
                     and checkpoint.get("iteration", 0) >= iteration_limit)
        if exhausted and additional_steps != 20:
            return web.json_response({"error": "Step budget reached; approve 20 more steps to continue"}, status=409)
        if additional_steps and not exhausted:
            raise ValueError("The current step budget has not been reached")
        record.config["iteration_limit"] = iteration_limit + additional_steps
        variables = checkpoint["variables"]
        if normalize_directory(variables.get("cwd")) != normalize_directory(workspace['path']):
            raise ValueError('Saved workflow directory does not match its owning task workspace')
        if variables.get("task_worktree"):
            from orchestrator.workflows.merge_request import _run
            root = normalize_directory(variables["task_worktree"])
            if root != normalize_directory(variables.get("cwd")):
                raise ValueError("Saved workflow directory does not match its task worktree")
            if await _run(root, "git", "branch", "--show-current") != variables.get("task_branch"):
                raise ValueError("The saved worktree is on a different branch")
            await _run(root, "git", "merge-base", "--is-ancestor", variables["task_baseline"], "HEAD")
        if checkpoint["next_step"] == "validate" or (
            workflow.review_policy and record.config.get("review_policy") != workflow.review_policy
        ):
            # Only an explicitly accepted Continue authorizes another QA pass.
            variables["validation_resume_request"] = token
        record.state["continuations"] = [*attempts, {
            "request_id": token, "previous_status": record.status,
            "previous_error": record.state.get("error"),
            "step": checkpoint["next_step"], "at": datetime.now(timezone.utc).isoformat(),
            "additional_steps": additional_steps, "iteration_limit": record.config["iteration_limit"],
        }]
        record.state.update(checkpoint=checkpoint, phase="accepted", error=None)
        if not await orch.store.claim_workflow_continuation(record):
            latest = await orch.store.get_workflow_run(record.run_id)
            if not latest or not any(a["request_id"] == token for a in latest.state.get("continuations", [])):
                workspace = await orch.store.workspace_record(task['workspace_id'])
                if workspace and workspace['status'] in {'merging', 'merge_recovery_required'}:
                    return web.json_response({'error': 'Workspace is reserved for merging or merge recovery; continue after it is released'}, status=409)
                return web.json_response({"error": "Run changed; refresh before continuing"}, status=409)
        else:
            record.status = "accepted"
            record.completed_at = None
            orch.tasks.schedule(task['task_id'], orch.tasks.execute(task['task_id'], continuation=record))
        return web.json_response({"run_id": record.run_id}, status=202)
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
