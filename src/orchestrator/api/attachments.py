"""Local-origin protected streaming attachment endpoints."""
from urllib.parse import unquote, quote
from aiohttp import web

from orchestrator.api.tasks import body


def service(request):
    return request.app['orch'].attachments


def public(row):
    return {k: v for k, v in row.items() if k not in {'storage_key', 'writer_pid', 'writer_started'}}


async def limits(request):
    return web.json_response(service(request).limits)


async def create(request):
    data = await body(request)
    return web.json_response(clean_draft(await service(request).create_draft(data.get('workspace_id'), data.get('task_id'))), status=201)


def clean_draft(draft):
    return {**draft, 'files': [public(row) for row in draft['files']]}


async def draft(request):
    return web.json_response(clean_draft(await service(request).draft(request.match_info['draft_id'])))


async def upload(request):
    name = unquote(request.headers.get('X-Filename', ''))
    result = await service(request).upload(request.match_info['draft_id'], request.match_info['upload_id'], name,
                                          request.content.iter_chunked(64 * 1024))
    return web.json_response(public(result), status=201)


async def remove(request):
    await service(request).remove_selection(request.match_info['draft_id'], request.match_info['upload_id'])
    return web.json_response({'status': 'deleted'})


async def listing(request):
    return web.json_response({'attachments': [public(row) for row in await service(request).listing(request.match_info['task_id'])],
                              'limits': service(request).limits})


async def content(request):
    svc = service(request)
    row = await svc.store.one('SELECT * FROM attachments WHERE attachment_id=? AND task_id=?',
                              (request.match_info['attachment_id'], request.match_info['task_id']))
    if not row:
        raise web.HTTPNotFound()
    if row['state'] in {'deleted', 'deleting'}:
        raise web.HTTPGone(text='Attachment permanently deleted during worktree cleanup', headers={'Cache-Control': 'no-store'})
    if row['state'] != 'ready':
        raise web.HTTPConflict(text='Attachment is not ready')
    path = svc.path(row['storage_key'])
    if not path.is_file():
        raise web.HTTPGone(text='Attachment content is unavailable', headers={'Cache-Control': 'no-store'})
    disposition = 'inline' if row['width'] else 'attachment'
    # Browser caches must not retain private attachment responses.
    response = web.FileResponse(path, headers={
        'Content-Type': row['media_type'], 'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'no-store', 'Content-Security-Policy': "default-src 'none'; sandbox",
        'Content-Disposition': f"{disposition}; filename=\"attachment\"; filename*=UTF-8''{quote(row['filename'], safe='')}",
    })
    return response


def register(app):
    app.router.add_get('/api/attachment-limits', limits)
    app.router.add_post('/api/attachment-drafts', create)
    app.router.add_get('/api/attachment-drafts/{draft_id}', draft)
    app.router.add_put('/api/attachment-drafts/{draft_id}/files/{upload_id}', upload)
    app.router.add_delete('/api/attachment-drafts/{draft_id}/files/{upload_id}', remove)
    app.router.add_get('/api/tasks/{task_id}/attachments', listing)
    app.router.add_get('/api/tasks/{task_id}/attachments/{attachment_id}/content', content)
