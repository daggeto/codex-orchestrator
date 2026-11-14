"""Durable feedback capture, assessment, approval and isolated task coordination."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from orchestrator.state.models import now
from urllib.parse import unquote, urlsplit, parse_qs

from aiohttp import web
from orchestrator.api.submissions import own_task
from orchestrator.tasks import Conflict
from orchestrator.transcripts import private_assessment_payload


class FeedbackService:
    def __init__(self, orch):
        self.orch = orch
        self.store = orch.store
        self.lock = asyncio.Lock()
        self.target_cache = None

    async def context(self, capture):
        route = capture.get('route', '')
        if not isinstance(route, str) or not route.startswith('/') or len(route) > 4096:
            raise ValueError('Expected a local page route')
        parsed = urlsplit(route)
        parts = parsed.path.strip('/').split('/')
        context = {'capture': capture, 'task': None, 'workspace': None, 'repository': None,
                   'agents': [], 'sessions': [], 'events': [], 'messages': [], 'workflow': None}
        task_id = None
        agent_id = None
        if len(parts) == 2:
            identity = unquote(parts[1])
            if parts[0] == 'tasks':
                task_id = identity
            elif parts[0] == 'agents':
                agent_id = identity
                task_id = parse_qs(parsed.query).get('task_id', [None])[0]
                if not await self.store.one('SELECT agent_id FROM agents WHERE agent_id=?', (agent_id,)):
                    raise LookupError('Session agent is unavailable')
            elif parts[0] == 'workspaces':
                context['workspace'] = await self.store.workspace_record(identity)
            elif parts[0] == 'repositories':
                context['repository'] = await self.store.one('SELECT * FROM repositories WHERE repository_id=?', (identity,))
        if task_id:
            task = await self.store.task(task_id)
            if not task:
                raise LookupError('Feedback task context is unavailable')
            agents = await self.store.task_agents(task_id)
            if agent_id and not any(a['agent_id'] == agent_id for a in agents):
                raise ValueError('Agent does not belong to this task')
            context.update(task=task, agents=agents, workspace=await self.store.workspace_record(task['workspace_id']))
            context['messages'] = await self.store.rows('SELECT * FROM task_messages WHERE task_id=? ORDER BY created_at', (task_id,))
            context['workflow'] = await self.store.one('SELECT run_id,workflow_name,status FROM workflow_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (task_id,))
            context['sessions'] = await self.store.rows('SELECT t.* FROM threads t JOIN task_threads p USING(thread_id) WHERE p.task_id=?', (task_id,))
        elif agent_id:
            context['agents'] = await self.store.rows('SELECT * FROM agents WHERE agent_id=?', (agent_id,))
            context['sessions'] = await self.store.rows('SELECT * FROM threads WHERE agent_id=?', (agent_id,))
            context['related_tasks'] = await self.store.rows('SELECT t.task_id,t.title,t.workspace_id,w.repository_id FROM tasks t JOIN workspaces w USING(workspace_id) WHERE t.task_id IN (SELECT task_id FROM task_agents WHERE agent_id=?)', (agent_id,))
        if task_id or agent_id:
            clauses, args = [], []
            if task_id:
                clauses.append('task_id=?'); args.append(task_id)
            if agent_id:
                clauses.append('agent_id=?'); args.append(agent_id)
            events = await self.store.rows('SELECT * FROM task_events WHERE ' + ' AND '.join(clauses) + ' ORDER BY id', tuple(args))
            private = await self.store.rows('SELECT thread_id FROM workflow_recoveries UNION SELECT thread_id FROM recovery_attempts WHERE action=?', ('Assess blocker against saved plan and source',))
            private_ids = {r['thread_id'] for r in private if r['thread_id']}
            for event in events:
                if event['thread_id'] in private_ids:
                    event['payload'] = private_assessment_payload(event['kind'], event.get('payload', {}))
                event.pop('payload_json', None)
            context['events'] = events
        if context['workspace']:
            context['repository'] = await self.store.one('SELECT * FROM repositories WHERE repository_id=?', (context['workspace']['repository_id'],))
        return context

    async def submit(self, data):
        if not isinstance(data, dict):
            raise ValueError('Expected feedback fields')
        for name in ('situation', 'improvements'):
            if not isinstance(data.get(name), str) or not 1 <= len(data[name].strip()) <= 20000:
                raise ValueError(f'{name.capitalize()} is required (maximum 20,000 characters)')
        key = data.get('idempotency_key')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', key):
            raise ValueError('A submission identity is required')
        capture = data.get('capture')
        if not isinstance(capture, dict):
            raise ValueError('Page capture is required')
        try:
            png = base64.b64decode(data.get('screenshot', ''), validate=True)
        except (ValueError, TypeError, binascii.Error):
            raise ValueError('Invalid screenshot') from None
        html = data.get('html')
        if (not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png) < 24 or png[12:16] != b'IHDR'
                or not int.from_bytes(png[16:20], 'big') or len(png) > 20 * 1024 * 1024):
            raise ValueError('A PNG screenshot under 20 MiB is required')
        if not isinstance(html, str) or not html.strip() or len(html.encode()) > 5 * 1024 * 1024:
            raise ValueError('An HTML snapshot under 5 MiB is required')
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        previous = await self.store.one('SELECT feedback_id,fingerprint FROM feedback WHERE idempotency_key=?', (key,))
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise Conflict('Submission identity belongs to different feedback')
            return previous['feedback_id']
        context = await self.context(capture)
        fid = uuid.uuid4().hex
        stamp = datetime.now(timezone.utc).isoformat()
        async with self.store.transaction() as db:
            previous = await (await db.execute('SELECT feedback_id,fingerprint FROM feedback WHERE idempotency_key=?', (key,))).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise Conflict('Submission identity belongs to different feedback')
                return previous['feedback_id']
            await db.execute('INSERT INTO feedback (feedback_id,idempotency_key,fingerprint,situation,improvements,context_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                             (fid, key, fingerprint, data['situation'].strip(), data['improvements'].strip(), json.dumps(context), stamp, stamp))
            for kind, media, contents in [('screenshot', 'image/png', png), ('html', 'text/plain; charset=utf-8', html.encode())]:
                await db.execute('INSERT INTO feedback_evidence VALUES (?,?,?,?)', (fid, kind, media, contents))
        return fid


    async def target_workspace(self):
        configured = self.orch.config.feedback_repository_path
        root = Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[3]
        if not configured and not (root / '.git').exists():
            raise ValueError('Configure feedback_repository_path to the Orch source repository')
        if not (root / 'src/orchestrator/core.py').is_file() or not (root / 'web/package.json').is_file():
            raise ValueError('Feedback target must be the Orch source repository')
        repo = await self.orch.workspaces.register(str(root))
        workspace = await self.store.one("SELECT * FROM workspaces WHERE repository_id=? AND kind='main'", (repo['repository_id'],))
        if not workspace or workspace['status'] != 'active':
            raise Conflict('Orch Main workspace is unavailable')
        self.target_cache = {key: workspace[key] for key in ('workspace_id', 'repository_id', 'path', 'branch')}
        return workspace

    async def reconcile(self, fid):
        async with self.lock:
            return await self._reconcile(fid)

    async def _reconcile(self, fid):
        row = await self.store.one('SELECT * FROM feedback WHERE feedback_id=?', (fid,))
        if not row:
            raise LookupError('Feedback not found')
        if row['status'] == 'analysing':
            task = await self.store.task(row['analysis_task_id']) if row['analysis_task_id'] else await self.store.one(
                'SELECT * FROM tasks WHERE idempotency_key=?', (f"feedback-analysis-{fid}-{row['revision']}",))
            status, error = None, None
            if task:
                if not row['analysis_task_id']:
                    await self.store.write('UPDATE feedback SET analysis_task_id=? WHERE feedback_id=? AND revision=?', (task['task_id'], fid, row['revision']))
                    row['analysis_task_id'] = task['task_id']
                if task['status'] == 'completed':
                    status = 'awaiting_input' if row['analysis'] and row['analysis']['questions'] else 'awaiting_review'
                    if not row['analysis']:
                        status, error = 'failed', 'Analysis finished without a valid brief'
                elif task['status'] in {'failed', 'interrupted', 'cancelled'}:
                    status, error = 'interrupted' if task['status'] == 'interrupted' else 'failed', task['phase']
            else:
                status, error = 'interrupted', 'Analysis acceptance interrupted; retry explicitly'
            if status:
                await self.store.write("UPDATE feedback SET status=?,error=?,updated_at=? WHERE feedback_id=? AND revision=? AND status='analysing'",
                                       (status, error, now(), fid, row['revision']))
                row = await self.store.one('SELECT * FROM feedback WHERE feedback_id=?', (fid,))
        if row['implementation_request'] and row['status'] != 'cancelled':
            request = row['implementation_request']
            task = await self.store.task(row['implementation_task_id']) if row['implementation_task_id'] else await self.store.one(
                'SELECT * FROM tasks WHERE idempotency_key=?', (request['idempotency_key'],))
            if task:
                workspace = await self.store.workspace_record(task['workspace_id'])
                run = await self.store.one('SELECT run_id,status,state_json FROM workflow_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (task['task_id'],))
                status = {'completed': 'completed', 'cancelled': 'cancelled', 'failed': 'implementation_failed',
                          'interrupted': 'implementation_interrupted'}.get(task['status'], 'implementing')
                if row['status'] != status or row['implementation_task_id'] != task['task_id']:
                    await self.store.write('UPDATE feedback SET status=?,implementation_task_id=?,error=?,completed_at=?,updated_at=? WHERE feedback_id=? AND revision=?',
                        (status, task['task_id'], task['phase'] if status in {'implementation_failed', 'implementation_interrupted'} else None,
                         task.get('completed_at') if status in {'completed', 'cancelled'} else None, now(), fid, row['revision']))
                    row = await self.store.one('SELECT * FROM feedback WHERE feedback_id=?', (fid,))
                row['implementation'] = {'task_id': task['task_id'], 'status': task['status'], 'phase': task['phase'],
                    'workspace': workspace, 'run_id': run['run_id'] if run else None,
                    'summary': (run['state'].get('final_variables', {}).get('summary') if run else None)}
            elif row['status'] == 'implementing':
                await self.store.write("UPDATE feedback SET status='implementation_interrupted',error=? WHERE feedback_id=? AND revision=?",
                    ('Implementation acceptance interrupted; retry the saved approval explicitly', fid, row['revision']))
                row = await self.store.one('SELECT * FROM feedback WHERE feedback_id=?', (fid,))
        return row

    async def ensure_analysis(self, fid):
        async with self.lock:
            row = await self._reconcile(fid)
            if row['status'] != 'submitted':
                return
            await self._launch(row, 'initial', '')

    async def _launch(self, row, kind, text, receipt=None):
        revision = row['revision'] + 1
        fid = row['feedback_id']
        async with self.store.transaction() as db:
            current = await (await db.execute('SELECT revision,status FROM feedback WHERE feedback_id=?', (fid,))).fetchone()
            if not current or current['revision'] != row['revision'] or current['status'] != row['status']:
                raise Conflict('Feedback changed before analysis acceptance')
            if receipt:
                await db.execute('INSERT INTO feedback_actions VALUES (?,?,?,?)', receipt)
            await db.execute('INSERT INTO feedback_revisions VALUES (?,?,?,?,?)',
                             (fid, revision, kind, json.dumps({'text': text, 'analysis': row['analysis']}), now()))
            await db.execute("UPDATE feedback SET revision=?,status='analysing',error=NULL,analysis_task_id=NULL,updated_at=? WHERE feedback_id=?", (revision, now(), fid))
        try:
            workspace = await self.target_workspace()
            task = await self.orch.tasks.submit(workspace['workspace_id'], 'Understand feedback: ' + row['situation'][:100],
                settings={'workflow': 'feedback-analysis', 'variables': {'feedback_id': fid, 'feedback_revision': revision}},
                isolation='never', idempotency_key=f'feedback-analysis-{fid}-{revision}')
            await self.store.write('UPDATE feedback SET analysis_task_id=? WHERE feedback_id=? AND revision=?', (task['task_id'], fid, revision))
        except Exception as exc:
            await self.store.write("UPDATE feedback SET status='failed',error=?,updated_at=? WHERE feedback_id=? AND revision=?", (str(exc), now(), fid, revision))

    async def _accept_implementation(self, row):
        request = row['implementation_request']
        try:
            task = await self.orch.tasks.submit(**request)
            await self.store.write("UPDATE feedback SET implementation_task_id=?,status='implementing',error=NULL,updated_at=? WHERE feedback_id=? AND revision=? AND status!='cancelled'",
                (task['task_id'], now(), row['feedback_id'], row['revision']))
        except Exception as exc:
            await self.store.write("UPDATE feedback SET status='implementation_failed',error=?,updated_at=? WHERE feedback_id=? AND revision=?",
                (str(exc), now(), row['feedback_id'], row['revision']))

    async def _approve(self, row, data, receipt):
        from orchestrator.workflows.feedback import RUNTIME_SAFETY
        if row['status'] != 'awaiting_review' or not row['analysis'] or row['analysis']['questions']:
            raise Conflict('Only the latest ready feedback brief can be approved')
        workspace = await self.target_workspace()
        if data.get('target_workspace_id') != workspace['workspace_id']:
            raise Conflict('Orch target changed. Reload and review the destination')
        revision = row['revision'] + 1
        analysis = row['analysis']
        approved = {'revision': row['revision'], 'analysis': analysis, 'target': self.target_cache, 'approved_at': now()}
        prompt = ('Feedback: ' + analysis['title'] + '\n\nApproved scope:\n' + analysis['brief'] +
            '\nCategory: ' + analysis['category'] + '\nReason: ' + analysis['rationale'] +
            '\nOriginal situation: ' + row['situation'] + '\nRequested improvements: ' + row['improvements'] +
            '\nRequirements: ' + analysis['requirement'] + '\nFrustration: ' + analysis['frustration'] +
            '\nEvidence: ' + analysis['evidence'] + '\nUncertainties: ' + analysis['uncertainties'] +
            '\n\n' + RUNTIME_SAFETY).strip()
        request = {'workspace_id': workspace['workspace_id'], 'prompt': prompt,
            'settings': {'workflow': 'feedback-implementation', 'variables': {'feedback_id': row['feedback_id'],
                'approved_feedback_revision': revision, 'create_mr': False}},
            'isolation': 'always', 'idempotency_key': f"feedback-implementation-{row['feedback_id']}-{revision}"}
        async with self.store.transaction() as db:
            current = await (await db.execute('SELECT revision,status FROM feedback WHERE feedback_id=?', (row['feedback_id'],))).fetchone()
            if not current or current['revision'] != row['revision'] or current['status'] != 'awaiting_review':
                raise Conflict('Feedback changed before approval')
            await db.execute('INSERT INTO feedback_actions VALUES (?,?,?,?)', receipt)
            await db.execute('INSERT INTO feedback_revisions VALUES (?,?,?,?,?)',
                (row['feedback_id'], revision, 'approve', json.dumps(approved), now()))
            await db.execute("UPDATE feedback SET revision=?,status='implementing',approved_json=?,implementation_request_json=?,error=NULL,updated_at=? WHERE feedback_id=?",
                (revision, json.dumps(approved), json.dumps(request), now(), row['feedback_id']))
        saved = await self.store.one('SELECT * FROM feedback WHERE feedback_id=?', (row['feedback_id'],))
        await self._accept_implementation(saved)

    async def action(self, fid, data):
        from orchestrator.workflows.feedback import validate_analysis
        if not isinstance(data, dict):
            raise ValueError('Expected feedback action')
        key = data.get('request_id')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', key):
            raise ValueError('A request identity is required')
        fingerprint = hashlib.sha256(json.dumps([fid, data], sort_keys=True).encode()).hexdigest()
        async with self.lock:
            previous = await self.store.one('SELECT * FROM feedback_actions WHERE request_id=?', (key,))
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise Conflict('Request identity belongs to another action')
                return await self._reconcile(fid)
            row = await self._reconcile(fid)
            if type(data.get('revision')) is not int or data['revision'] != row['revision']:
                raise Conflict('Feedback changed. Reload before applying this action')
            action = data.get('action')
            if row['status'] in {'completed', 'cancelled'}:
                raise Conflict('Feedback is already completed or cancelled')
            if action == 'approve':
                await self._approve(row, data, (key, fid, fingerprint, now()))
                return await self._reconcile(fid)
            if action == 'retry_implementation':
                if not row['implementation_request'] or row['implementation_task_id'] or row['status'] not in {'implementation_failed', 'implementation_interrupted'}:
                    raise Conflict('Open the existing implementation task to continue or recover it')
                await self.store.write('INSERT INTO feedback_actions VALUES (?,?,?,?)', (key, fid, fingerprint, now()))
                await self._accept_implementation(row)
                return await self._reconcile(fid)
            if action == 'cancel':
                if row['implementation_task_id']:
                    await self.orch.tasks.cancel(row['implementation_task_id'])
                elif row['analysis_task_id']:
                    await self.orch.tasks.cancel(row['analysis_task_id'])
            elif row['implementation_request']:
                raise Conflict('This feedback already has an approved implementation')
            elif row['status'] == 'analysing':
                raise Conflict('Wait for the active analysis to finish')
            elif action in {'answer', 'revise', 'retry'}:
                if action == 'answer' and row['status'] != 'awaiting_input':
                    raise Conflict('Feedback is not awaiting answers')
                if action == 'retry' and row['status'] not in {'submitted', 'failed', 'interrupted'}:
                    raise Conflict('This feedback does not need a retry')
                if action != 'retry' and (not isinstance(data.get('text'), str) or not 1 <= len(data['text'].strip()) <= 20000):
                    raise ValueError('Provide answers or revision instructions (maximum 20,000 characters)')
            elif action == 'edit':
                if row['status'] != 'awaiting_review':
                    raise Conflict('Only a ready brief can be edited')
                analysis = validate_analysis(data.get('analysis'))
                if analysis['questions']:
                    raise ValueError('Resolve questions through analyst clarification')
            else:
                raise ValueError('Unknown feedback action')
            if action in {'answer', 'revise', 'retry'}:
                # Reserve the revision before starting the normal task lifecycle.
                await self._launch(row, action, data.get('text', ''), (key, fid, fingerprint, now()))
            else:
                revision = row['revision'] + 1
                async with self.store.transaction() as db:
                    current = await (await db.execute('SELECT revision,status FROM feedback WHERE feedback_id=?', (fid,))).fetchone()
                    if not current or current['revision'] != row['revision'] or current['status'] != row['status']:
                        raise Conflict('Feedback changed before this action')
                    await db.execute('INSERT INTO feedback_actions VALUES (?,?,?,?)', (key, fid, fingerprint, now()))
                    await db.execute('INSERT INTO feedback_revisions VALUES (?,?,?,?,?)',
                                     (fid, revision, action, json.dumps({'analysis': row['analysis']}), now()))
                    await db.execute('UPDATE feedback SET revision=?,status=?,analysis_json=?,updated_at=? WHERE feedback_id=?',
                                     (revision, 'cancelled' if action == 'cancel' else 'awaiting_review',
                                      json.dumps(row['analysis'] if action == 'cancel' else analysis), now(), fid))
                    if action == 'cancel':
                        await db.execute('UPDATE feedback SET completed_at=? WHERE feedback_id=?', (now(), fid))
            return await self._reconcile(fid)


async def submit(request):
    request._client_max_size = 34 * 1024 * 1024
    data = await request.json()
    async def accept():
        fid = await request.app['orch'].feedback.submit(data)
        await request.app['orch'].feedback.ensure_analysis(fid)
        return web.json_response({'feedback_id': fid}, status=202)
    return await asyncio.shield(own_task(request.app, accept()))


async def listing(request):
    completed = request.query.get('tab') == 'completed'
    service = request.app['orch'].feedback
    pending = await service.store.rows("SELECT feedback_id FROM feedback WHERE implementation_request_json IS NOT NULL AND status NOT IN ('completed','cancelled')")
    for item in pending:
        await service.reconcile(item['feedback_id'])
    rows = await service.store.rows("SELECT feedback_id FROM feedback WHERE " +
        ("status IN ('completed','cancelled')" if completed else "status NOT IN ('completed','cancelled')") + ' ORDER BY created_at DESC LIMIT 200')
    rows = [await service.reconcile(row['feedback_id']) for row in rows]
    return web.json_response({'items': [{key: row[key] for key in ('feedback_id', 'situation', 'status', 'created_at', 'analysis', 'revision')} for row in rows]})


async def detail(request):
    row = await request.app['orch'].feedback.reconcile(request.match_info['feedback_id'])
    service = request.app['orch'].feedback
    try:
        if service.target_cache is None:
            await service.target_workspace()
        row['target'] = service.target_cache
    except (ValueError, RuntimeError, OSError, Conflict) as exc:
        row['target'] = None
        row['target_error'] = str(exc)
    row['history'] = await request.app['orch'].store.rows('SELECT revision,kind,contents_json,created_at FROM feedback_revisions WHERE feedback_id=? ORDER BY revision', (row['feedback_id'],))
    row.pop('fingerprint'); row.pop('idempotency_key')
    return web.json_response(row)


async def evidence(request):
    kind = request.match_info['kind']
    if kind not in {'screenshot', 'html'}:
        raise LookupError('Evidence not found')
    row = await request.app['orch'].store.one('SELECT * FROM feedback_evidence WHERE feedback_id=? AND kind=?', (request.match_info['feedback_id'], kind))
    if not row:
        raise LookupError('Evidence not found')
    return web.Response(body=row['contents'], headers={'Content-Type': row['media_type'],
        'Content-Disposition': f'attachment; filename="feedback.{"png" if kind == "screenshot" else "html"}"',
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox", 'Cache-Control': 'no-store'})


async def action(request):
    data = await request.json()
    async def accept():
        row = await request.app['orch'].feedback.action(request.match_info['feedback_id'], data)
        return web.json_response({'feedback_id': row['feedback_id'], 'revision': row['revision']}, status=202)
    return await asyncio.shield(own_task(request.app, accept()))


def register(app):
    app.router.add_post('/api/feedback', submit)
    app.router.add_post('/api/feedback/{feedback_id}/actions', action)
    app.router.add_get('/api/feedback', listing)
    app.router.add_get('/api/feedback/{feedback_id}', detail)
    app.router.add_get('/api/feedback/{feedback_id}/evidence/{kind}', evidence)
