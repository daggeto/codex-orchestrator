"""Durable developer approval, independent of live human-input waiters."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from orchestrator.workflows.steps import Step, StepResult, task_snapshot


class PlanApprovalStep(Step):
    def __init__(self, step_id, author_role, sources, targets, next_step, **kwargs):
        super().__init__(step_id, **kwargs)
        self.author_role, self.sources = author_role, sources
        self.targets, self.next_step = targets, next_step

    async def execute(self, ctx):
        state = dict(ctx.variables.get('plan_approval') or {})
        action = ctx.variables.pop('_plan_action', None)
        if not state:
            state = {'kind': 'plan_approval', 'id': uuid.uuid4().hex, 'step_id': self.step_id,
                     'author': self.author_role, 'targets': self.targets, 'snapshot': None,
                     'status': 'rerouting', 'messages': [], 'revisions': [], 'revision': 0,
                     'markdown': ''}
        try:
            snapshot = await task_snapshot(ctx)
            if not state or state.get('status') == 'rerouting':
                previous = state
                state = {**previous, 'kind': 'plan_approval', 'id': previous.get('id', uuid.uuid4().hex),
                         'step_id': self.step_id, 'author': self.author_role, 'targets': self.targets,
                         'snapshot': snapshot, 'status': 'waiting', 'messages': previous.get('messages', []),
                         'revisions': previous.get('revisions', [])}
                text = str(ctx.variables[self.sources[-1]])
                if len(self.sources) > 1:
                    text += '\n\n## Supporting planning criteria\n\n' + '\n\n'.join(str(ctx.variables[key]) for key in self.sources[:-1])
                revise(state, text, self.author_role)
            if action:
                if action['action'] != 'send_back' and snapshot != state.get('snapshot'):
                    raise ValueError('Checkout changed since planning. Send feedback to a planning agent to refresh the plan.')
                if action['action'] == 'approve':
                    if not state.get('markdown', '').strip():
                        raise ValueError('Save a nonempty plan before approving')
                    state.update(status='approved', approved_revision=state['revision'])
                    return StepResult(True, {'plan_approval': state, 'approved_plan': state['markdown']}, next_step=self.next_step)
                if action['action'] == 'send_back':
                    state['status'] = 'rerouting'
                    return StepResult(True, {'plan_approval': state, 'approved_plan': '',
                        'planning_targets': list(self.targets), 'planning_feedback': action['message'] + '\n\nCurrent developer draft:\n' + state['markdown']}, next_step=action['target'])
                if action['action'] == 'chat':
                    agent = ctx.get_agent_for_role(self.author_role)
                    thread = await ctx.acquire_thread(agent, self.step_id + '-discussion', cwd=ctx.variables.get('cwd'))
                    await ctx.persist_step_thread(agent, thread)
                    message = ('Discuss the plan; do not edit repository files or approve implementation. '
                        'Return only a JSON object with reply (string) and markdown (complete revised plan string, or null if unchanged). '
                        'Preserve developer edits and original intent; make substitutions and scope reductions explicit.\nOriginal request:\n'
                        + ctx.variables['task_description'] + '\nCurrent plan:\n' + state['markdown']
                        + '\nDiscussion:\n' + json.dumps(state['messages']))
                    before = snapshot
                    response = await agent.send_message(thread, message)
                    if not response.ok:
                        raise ValueError(response.error or 'Plan discussion failed')
                    if await task_snapshot(ctx) != before:
                        raise ValueError('Checkout changed during plan discussion')
                    result = json.loads(response.full_text)
                    if not isinstance(result.get('reply'), str) or not (result.get('markdown') is None or isinstance(result['markdown'], str) and result['markdown'].strip()):
                        raise ValueError('Plan author returned an invalid response; saved draft retained')
                    state['messages'].append({'role': 'assistant', 'text': result['reply']})
                    if result.get('markdown'):
                        revise(state, result['markdown'], self.author_role)
            state.update(status='waiting', error=None)
        except (ValueError, RuntimeError, OSError, TypeError, AttributeError) as exc:
            state.update(status='waiting', error=str(exc))
        return StepResult(True, {'plan_approval': state, '_plan_action': None}, pause_reason='waiting_for_plan_approval')


def revise(state, markdown, author):
    state['revision'] = state.get('revision', 0) + 1
    state['markdown'] = markdown
    state.setdefault('revisions', []).append({'revision': state['revision'], 'markdown': markdown,
        'author': author, 'at': datetime.now(timezone.utc).isoformat()})


async def action(request):
    import asyncio
    from orchestrator.api.submissions import own_task
    return await asyncio.shield(own_task(request.app, _accept_action(request)))


async def _accept_action(request):
    """Accept a revision-bound developer operation and durably queue execution."""
    from aiohttp import web
    from orchestrator.tasks import Conflict
    orch = request.app['orch']
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object')
    kind = data.get('action')
    if kind not in {'save', 'chat', 'send_back', 'approve'}:
        raise ValueError('Unknown plan action')
    key = data.get('idempotency_key')
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValueError('An idempotency key is required')
    tid = request.match_info['task_id']
    async with orch.tasks.accept_lock:
        task = await orch.store.task(tid)
        if not task:
            raise LookupError('Task not found')
        row = await orch.store.one('SELECT run_id FROM workflow_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (tid,))
        if not row:
            raise Conflict('Task has no workflow')
        run = await orch.store.get_workflow_run(row['run_id'])
        variables = run.state.get('checkpoint', {}).get('variables', {})
        state = variables.get('plan_approval')
        if not state or data.get('approval_id') != state['id']:
            raise Conflict('Plan approval is no longer active')
        prior = state.setdefault('actions', {}).get(key)
        if prior:
            if prior != data:
                raise Conflict('Action identity already used for different content')
            return web.json_response({'status': 'accepted'})
        if task['status'] != 'waiting_input' or task['archived_at'] or run.state.get('phase') != 'waiting_for_plan_approval' or tid in orch.tasks.running:
            raise Conflict('Plan is busy or no longer awaiting approval')
        if data.get('revision') != state['revision']:
            raise Conflict('Plan revision changed. Your draft is retained; load the latest revision.')
        workspace = await orch.store.workspace_record(task['workspace_id'])
        if workspace['status'] != 'active':
            raise Conflict('Workspace is merging or unavailable')
        workflow = orch.workflows.get(run.workflow_name)
        if workflow is None:
            raise Conflict('Workflow definition is unavailable')
        step = workflow.get_step(state['step_id'])
        if not isinstance(step, PlanApprovalStep):
            raise Conflict('Plan approval definition is no longer available; restart this task')
        if kind == 'send_back' and data.get('target') not in step.targets:
            raise ValueError('Invalid planning target')
        if kind in {'chat', 'send_back'} and (not isinstance(data.get('message'), str) or not data['message'].strip()):
            raise ValueError('Feedback is required')
        if kind == 'save':
            if not isinstance(data.get('markdown'), str) or not data['markdown'].strip():
                raise ValueError('Plan cannot be empty')
            revise(state, data['markdown'], 'developer')
        else:
            if kind in {'chat', 'send_back'}:
                state['messages'].append({'role': 'developer', 'text': data['message']})
            variables['_plan_action'] = data
            state['status'] = 'queued'
            run.status = 'accepted'
            run.state['phase'] = 'accepted'
        state['actions'][key] = data
        run.state['variables'] = variables
        run.state['final_variables'] = variables
        async with orch.store.transaction() as db:
            current = await (await db.execute('SELECT updated_at FROM workflow_runs WHERE run_id=?', (run.run_id,))).fetchone()
            latest = await (await db.execute('SELECT status FROM tasks WHERE task_id=?', (tid,))).fetchone()
            if current['updated_at'] != run.updated_at or latest['status'] != 'waiting_input':
                raise Conflict('Plan changed while accepting this action; refresh before retrying')
            await db.execute('UPDATE workflow_runs SET status=?,state_json=?,updated_at=? WHERE run_id=?',
                (run.status, json.dumps(run.state), datetime.now(timezone.utc).isoformat(), run.run_id))
            if kind != 'save':
                await db.execute("UPDATE tasks SET status='queued',phase='Plan action queued' WHERE task_id=?", (tid,))
        if kind != 'save':
            orch.tasks.schedule(tid, orch.tasks.execute(tid, continuation=run))
        return web.json_response({'status': 'saved' if kind == 'save' else 'accepted'}, status=200 if kind == 'save' else 202)
