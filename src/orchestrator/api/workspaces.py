"""Persistent repository and workspace navigation."""
from pathlib import Path
from aiohttp import web
from orchestrator.api.filesystem import permitted
from orchestrator.api.tasks import body
from orchestrator.api.submissions import own_task
import asyncio


async def repositories(request):
    orch = request.app['orch']
    records = await orch.store.repositories()
    for record in records:
        record['available'] = Path(record['main_path']).is_dir()
    return web.json_response({'repositories': records})


async def register_repository(request):
    data = await body(request)
    orch = request.app['orch']
    path = permitted(orch, data.get('path', ''))
    repository = await orch.workspaces.register(str(path))
    await orch.store.register_project(repository['main_path'])
    return web.json_response({'repository': repository}, status=201)


async def workspaces(request):
    orch = request.app['orch']
    rid = request.match_info['repository_id']
    repository = await orch.store.one('SELECT * FROM repositories WHERE repository_id=?', (rid,))
    if not repository:
        raise LookupError('Repository not found')
    if Path(repository['main_path']).is_dir():
        await orch.workspaces.register(repository['main_path'])
    records = await orch.store.workspaces(rid)
    for record in records:
        record['available'] = Path(record['path']).is_dir()
        record['archive'] = await orch.store.one('SELECT * FROM archives WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1', (record['workspace_id'],))
    return web.json_response({'workspaces': records})


async def delete(request):
    async def accept():
        service = request.app['orch'].archives
        workspace, aid = await service.begin(request.match_info['workspace_id'])
        own_task(request.app, service.execute_archive(workspace, aid))
        return web.json_response({'archive_id': aid, 'status_url': '/api/workspaces/' + workspace['workspace_id'] + '/archive'}, status=202)
    return await asyncio.shield(own_task(request.app, accept()))


async def archive_status(request):
    record = await request.app['orch'].store.one('SELECT * FROM archives WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1', (request.match_info['workspace_id'],))
    if not record:
        raise LookupError('Archive operation not found')
    return web.json_response({'archive': record})


async def navigation(request):
    store = request.app['orch'].store
    workspaces = await store.rows('SELECT * FROM workspaces ORDER BY kind,path')
    for workspace in workspaces:
        workspace['archive'] = await store.one('SELECT * FROM archives WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1', (workspace['workspace_id'],))
    return web.json_response({'repositories': await store.repositories(), 'workspaces': workspaces,
        'tasks': await store.rows('SELECT * FROM tasks WHERE task_id IN (SELECT task_id FROM (SELECT task_id, ROW_NUMBER() OVER (PARTITION BY workspace_id ORDER BY task_id DESC) AS position FROM tasks) WHERE position<=20) ORDER BY task_id DESC'),
        'agents': await store.rows('SELECT agent_id,task_id,role,status,retired_at FROM agents WHERE task_id IS NOT NULL UNION SELECT a.agent_id,p.task_id,a.role,a.status,a.retired_at FROM task_agents p JOIN agents a USING(agent_id)')})


async def merge_eligibility(request):
    service = request.app['orch'].merges
    await service.reconcile()
    return web.json_response(await service.preflight(request.match_info['workspace_id']))


async def merge_status(request):
    service = request.app['orch'].merges
    await service.reconcile()
    operation = await service.store.merge_operation(request.match_info['operation_id'])
    if not operation or request.match_info['workspace_id'] not in {operation['source_workspace_id'], operation['destination_workspace_id']}:
        raise LookupError('Merge operation not found for this workspace')
    return web.json_response({'operation': operation})


async def merge(request):
    data = await body(request)
    async def accept():
        service = request.app['orch'].merges
        await service.reconcile()
        operation, created = await service.begin(request.match_info['workspace_id'], data)
        if created:
            own_task(request.app, service.execute(operation['operation_id']))
        return web.json_response({'operation': operation, 'status_url':
            '/api/workspaces/' + operation['source_workspace_id'] + '/merges/' + operation['operation_id']}, status=202)
    return await asyncio.shield(own_task(request.app, accept()))


def register(app):
    app.router.add_get('/api/workspaces/{workspace_id}/merge', merge_eligibility)
    app.router.add_post('/api/workspaces/{workspace_id}/merge', merge)
    app.router.add_get('/api/workspaces/{workspace_id}/merges/{operation_id}', merge_status)
    app.router.add_get('/api/navigation', navigation)
    app.router.add_get('/api/workspaces/{workspace_id}/archive', archive_status)
    app.router.add_get('/api/repositories', repositories)
    app.router.add_post('/api/repositories', register_repository)
    app.router.add_get('/api/repositories/{repository_id}/workspaces', workspaces)
    app.router.add_delete('/api/workspaces/{workspace_id}', delete)
