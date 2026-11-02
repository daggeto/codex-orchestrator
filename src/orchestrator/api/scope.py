"""Scope split presentation and explicit, idempotent follow-up task acceptance."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from aiohttp import web

from orchestrator.api.submissions import own_task
from orchestrator.tasks import Conflict
from orchestrator.workflows.scope import proposal_key, proposals


def saved_variables(run):
    state = run.get('state') or {}
    return state.get('final_variables') or state.get('variables') or state.get('checkpoint', {}).get('variables') or {}


def saved_proposals(run):
    variables = saved_variables(run)
    raw = variables.get('scope_suggestions')
    if not raw:
        return [], variables.get('scope_suggestions_error', '')
    try:
        return proposals(raw, run['workflow_name']), ''
    except (ValueError, TypeError) as exc:
        return [], str(exc)


async def presentation(orch, task, run):
    if not run:
        return None
    items, error = saved_proposals(run)
    if not items and not error:
        return None
    source = await orch.store.workspace_record(task['workspace_id'])
    destination = await orch.store.one(
        "SELECT * FROM workspaces WHERE repository_id=? AND kind='main'", (source['repository_id'],)
    ) if source else None
    keys = [proposal_key(task['task_id'], run['run_id'], item['id']) for item in items]
    created = await orch.store.rows(
        'SELECT task_id,idempotency_key FROM tasks WHERE idempotency_key IN (' + ','.join('?' for _ in keys) + ')', keys
    ) if keys else []
    launched = {row['idempotency_key']: row['task_id'] for row in created}
    for item, key in zip(items, keys):
        item['task_id'] = launched.get(key)
        definition = orch.workflows.get(item['workflow'])
        item['available'] = bool(definition and definition.publication_policy != 'required')
    variables = saved_variables(run)
    return {
        'run_id': run['run_id'], 'suggestions': items, 'error': error,
        'source_path': source['path'] if source else None,
        'source_branch': source['branch'] if source else None,
        'source_commit': variables.get('task_commit') or variables.get('task_baseline'),
        'destination_path': destination['path'] if destination else None,
        'can_start': bool(destination and destination['status'] == 'active'
                          and Path(destination['path']).is_dir()
                          and not task['archived_at'] and task['status'] in {'failed', 'interrupted', 'cancelled'}
                          and run['status'] in {'failed', 'interrupted', 'cancelled', 'paused'}),
    }


async def start(request):
    # Acceptance belongs to the service even if the client disconnects.
    return await asyncio.shield(own_task(request.app, accept(request)))


async def accept(request):
    from orchestrator.api.tasks import body
    orch = request.app['orch']
    task_id = request.match_info['task_id']
    suggestion_id = request.match_info['suggestion_id']
    data = await body(request)
    run_id = data.get('run_id')
    if not isinstance(run_id, str) or not run_id or not re.fullmatch(r'[a-f0-9]{20}', suggestion_id):
        raise ValueError('A saved run and suggestion are required')
    source = await orch.store.task(task_id)
    if not source:
        raise LookupError('Source task not found')
    key = proposal_key(task_id, run_id, suggestion_id)
    previous = await orch.store.one('SELECT task_id FROM tasks WHERE idempotency_key=?', (key,))
    if previous:
        return web.json_response({'task_id': previous['task_id']}, status=202)
    run = await orch.store.one('SELECT * FROM workflow_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (task_id,))
    if not run or run['run_id'] != run_id:
        raise Conflict('The source run changed. Refresh its scope suggestions.')
    view = await presentation(orch, source, run)
    item = next((item for item in (view or {}).get('suggestions', []) if item['id'] == suggestion_id), None)
    if not item:
        raise LookupError('Saved suggestion not found')
    if not view['can_start']:
        raise Conflict('The source must be stopped and unarchived, with an active repository Main workspace')
    if not item['available']:
        raise Conflict('The suggested local workflow is unavailable')
    answers = data.get('answers', [])
    if not isinstance(answers, list) or len(answers) != len(item['required_inputs']) or any(
        not isinstance(answer, str) or not answer.strip() or len(answer) > 16000 for answer in answers
    ):
        raise ValueError('Answer each required question before starting this task')
    prompt = (item['prompt'] + '\n\nContext from the scope split:\n' + item['context']
              + '\n\nScope decision: ' + item['reason']
              + '\nSource task: ' + task_id + '\nSource run: ' + run_id
              + '\nSource checkout: ' + str(view['source_path']) + '\nSource branch: ' + str(view['source_branch'])
              + '\nSource commit at scope checkpoint: ' + str(view['source_commit'] or 'Unavailable; inspect the source report')
              + '\nThis is a separate task in a new worktree from repository Main. Source changes are not automatically copied. '
                'Inspect relevant source commits if needed; do not assume sibling proposals have run. '
                'Follow the repository validation policy. Do not push, publish, or merge.')
    for question, answer in zip(item['required_inputs'], answers):
        prompt += '\n\n' + question + '\n' + answer.strip()
    destination = await orch.store.one(
        "SELECT w.* FROM workspaces w JOIN workspaces source ON source.repository_id=w.repository_id "
        "WHERE source.workspace_id=? AND w.kind='main'", (source['workspace_id'],))
    if not destination:
        raise Conflict('Repository Main workspace is no longer available. Refresh and retry.')
    task = await orch.tasks.submit(
        destination['workspace_id'], prompt, 'workflow',
        {'workflow': item['workflow'], 'variables': {'create_mr': False},
         'scope_origin': {'task_id': task_id, 'run_id': run_id, 'suggestion_id': suggestion_id}},
        isolation='always', idempotency_key=key, copy_from_task_id=task_id)
    return web.json_response({'task_id': task['task_id']}, status=202)
