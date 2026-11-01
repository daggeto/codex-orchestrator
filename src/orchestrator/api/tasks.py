"""Repository-centered task HTTP contracts."""
from __future__ import annotations

import asyncio
from aiohttp import web

from orchestrator.api.submissions import own_task
from orchestrator.tasks import Conflict


async def body(request):
    value = await request.json()
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except Conflict as exc:
        return web.json_response({'error': str(exc), 'details': getattr(exc, 'details', None)}, status=409)
    except LookupError as exc:
        return web.json_response({'error': str(exc)}, status=404)
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        return web.json_response({'error': str(exc)}, status=400)


async def submit(request):
    data = await body(request)
    settings = data.get('settings', {})
    if not isinstance(settings, dict):
        raise ValueError('settings must be an object')
    store = request.app['orch'].store
    workspace = await store.workspace_record(data.get('workspace_id'))
    if not workspace:
        raise LookupError('Workspace not found')
    if data.get('repository_id') and workspace['repository_id'] != data['repository_id']:
        raise ValueError('Workspace does not belong to repository')
    async def accept():
        task = await request.app['orch'].tasks.submit(workspace['workspace_id'], data.get('prompt'), data.get('kind', 'workflow'),
                                                    settings, data.get('isolation', 'auto'), data.get('idempotency_key'), data.get('attachment_ids'), data.get('draft_id'))
        return web.json_response({'task_id': task['task_id'], 'status_url': '/api/tasks/' + task['task_id']}, status=202)
    return await asyncio.shield(own_task(request.app, accept()))


async def detail(request):
    orch = request.app['orch']
    tid = request.match_info['task_id']
    task = await orch.store.task(tid)
    if not task:
        raise LookupError('Task not found')
    from orchestrator.api.attachments import public
    agents = await orch.store.task_agents(tid)
    for agent in agents:
        runtime = orch.registry.get_agent(agent['agent_id'])
        agent['activity'] = runtime.activity if runtime else {'state': 'idle', 'threads': [], 'error': None}
    return web.json_response({'task': task, 'agents': agents,
        'workflow': await orch.store.one('SELECT * FROM workflow_runs WHERE task_id=?', (tid,)),
        'steps': await orch.store.rows('SELECT * FROM step_executions WHERE task_id=? ORDER BY created_at', (tid,)),
        'attachments': [public(row) for row in await orch.store.rows('SELECT * FROM attachments WHERE task_id=? ORDER BY created_at,attachment_id', (tid,))],
        'messages': await orch.store.rows('SELECT m.*, (SELECT json_group_array(attachment_id) FROM (SELECT attachment_id FROM message_attachments WHERE message_id=m.message_id ORDER BY position)) AS attachment_ids_json FROM task_messages m WHERE task_id=? ORDER BY created_at', (tid,)),
        'pending_input': await orch.store.pending_inputs(tid)})


async def history(request):
    store = request.app['orch'].store
    wid = request.match_info['workspace_id']
    if not await store.workspace_record(wid):
        raise LookupError('Workspace not found')
    return web.json_response(await task_listing(request, workspace_id=wid, default_archive='all'))


async def task_listing(request, workspace_id=None, default_archive='exclude'):
    return await request.app['orch'].store.task_page(
        workspace_id=workspace_id or request.query.get('workspace_id'),
        repository_id=request.query.get('repository_id'), status=request.query.get('status'),
        archived=request.query.get('archived', default_archive),
        before=request.query.get('before') or None, limit=int(request.query.get('limit', 50)))


async def listing(request):
    return web.json_response(await task_listing(request))


async def archive(request):
    data = await body(request)
    if type(data.get('archived')) is not bool:
        raise ValueError('archived must be a boolean')
    task = await request.app['orch'].tasks.set_archived(request.match_info['task_id'], data['archived'])
    return web.json_response({'task': task})


async def message(request):
    data = await body(request)
    await asyncio.shield(own_task(request.app, request.app['orch'].tasks.send_message(request.match_info['task_id'], data.get('message', ''), data.get('attachment_ids'), data.get('draft_id'), data.get('idempotency_key'))))
    return web.json_response({'status': 'queued'}, status=202)


async def complete(request):
    await request.app['orch'].tasks.complete(request.match_info['task_id'])
    return web.json_response({'status': 'completed'})


async def cancel(request):
    orch = request.app['orch']
    tid = request.match_info['task_id']
    if not await orch.store.task(tid):
        raise LookupError('Task not found')
    await orch.tasks.cancel(tid)
    return web.json_response({'status': 'cancelled'})


async def resolve(request):
    data = await body(request)
    if 'response' not in data:
        raise ValueError('A response is required')
    await asyncio.shield(own_task(request.app, request.app['orch'].tasks.resolve_input(request.match_info['request_id'], data['response'], data.get('attachment_ids'), data.get('draft_id'))))
    return web.json_response({'status': 'resolved'})


async def events(request):
    store = request.app['orch'].store
    aid = request.match_info['agent_id']
    agent = await store.one('SELECT * FROM agents WHERE agent_id=?', (aid,))
    if not agent:
        raise LookupError('Agent not found')
    rows = await store.events(aid, int(request.query.get('after', 0)), int(request.query.get('limit', 200)), request.query.get('task_id'))
    return web.json_response({'agent': agent, 'events': rows, 'next_cursor': rows[-1]['id'] if rows else int(request.query.get('after', 0))})


def register(app):
    app.router.add_get('/api/tasks', listing)
    app.router.add_post('/api/tasks', submit)
    app.router.add_post('/api/tasks/{task_id}/archive', archive)
    app.router.add_get('/api/tasks/{task_id}', detail)
    app.router.add_post('/api/tasks/{task_id}/messages', message)
    app.router.add_post('/api/tasks/{task_id}/complete', complete)
    app.router.add_post('/api/tasks/{task_id}/cancel', cancel)
    app.router.add_get('/api/workspaces/{workspace_id}/tasks', history)
    app.router.add_post('/api/input-requests/{request_id}/resolve', resolve)
    app.router.add_get('/api/agents/{agent_id}/events', events)
