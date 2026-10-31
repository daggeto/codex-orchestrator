"""Service-owned workflow and conversational task lifecycle."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import json
import uuid

from orchestrator.roles.loader import RoleConfig
from orchestrator.state.models import now
from orchestrator.state.store import WorkflowRunRecord


class Conflict(RuntimeError):
    pass


TERMINAL = {'completed', 'failed', 'cancelled'}


class TaskService:
    def __init__(self, orch):
        self.orch = orch
        self.store = orch.store
        self.running = {}
        self.accept_lock = asyncio.Lock()
        self.input_waiters = {}

    def schedule(self, task_id, operation):
        job = asyncio.create_task(operation)
        self.running[task_id] = job
        def finished(done):
            if self.running.get(task_id) is done:
                self.running.pop(task_id, None)
            if not done.cancelled():
                done.exception()
        job.add_done_callback(finished)
        return job

    def role(self, settings):
        selected = settings.get('role')
        if selected and selected not in self.orch.roles:
            raise ValueError('Unknown agent role')
        config = asdict(self.orch.roles[selected]) if selected else asdict(RoleConfig(
            name='default', model=self.orch.config.default_model,
            approval_policy=self.orch.config.default_approval_policy,
            sandbox=self.orch.config.default_sandbox))
        if config['backend'] == 'codex':
            config['model'] = config['model'] or self.orch.config.default_model
        recipe = settings.get('recipe')
        if recipe:
            skill = self.orch.skills.get(recipe)
            if not skill or (skill.roles and config['name'] not in skill.roles):
                raise ValueError('Recipe is unavailable for this role')
            config['system_prompt'] += '\n\n' + skill.build_prompt(settings.get('variables', {}))
            if skill.settings.get('model'):
                config['model'] = skill.settings['model']
            if skill.settings.get('effort'):
                config['config_overrides']['model_reasoning_effort'] = skill.settings['effort']
        if settings.get('model'):
            if not isinstance(settings['model'], str) or not settings['model'].strip():
                raise ValueError('Model must be a nonempty string')
            config['model'] = settings['model']
        if settings.get('effort'):
            if config['backend'] != 'codex' or settings['effort'] not in {'minimal','low','medium','high','xhigh','max'}:
                raise ValueError('Unsupported reasoning effort for backend')
            config['config_overrides']['model_reasoning_effort'] = settings['effort']
        return config

    async def submit(self, workspace_id, prompt, kind='workflow', settings=None, isolation='auto', idempotency_key=None):
        settings = dict(settings or {})
        if not isinstance(settings.get('variables', {}), dict):
            raise ValueError('variables must be an object')
        if kind not in {'workflow','agent'} or isolation not in {'auto','always','never'}:
            raise ValueError('Invalid task kind or isolation policy')
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('A prompt is required')
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128):
            raise ValueError('Invalid idempotency key')
        prompt = prompt.strip()
        fingerprint = hashlib.sha256(json.dumps([workspace_id, prompt, kind, settings, isolation], sort_keys=True).encode()).hexdigest()
        async with self.accept_lock:
            if idempotency_key:
                previous = await self.store.one('SELECT * FROM tasks WHERE idempotency_key=?', (idempotency_key,))
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise Conflict('Idempotency key belongs to a different submission')
                    return previous
            if idempotency_key and await self.store.get_workflow_run(idempotency_key):
                raise Conflict('Run ID belongs to legacy workflow history')
            workspace = await self.store.workspace_record(workspace_id)
            if not workspace:
                raise LookupError('Workspace not found')
            if workspace['status'] != 'active':
                raise Conflict('Workspace is being archived or unavailable')
            if kind == 'workflow':
                workflow = self.orch.workflows.get(settings.get('workflow', 'feature'))
                if not workflow:
                    raise ValueError('Unknown workflow')
                settings['workflow'] = workflow.name
                settings['roles'] = {name: self.role({'role': name}) for name in workflow.required_roles}
                variables = settings.setdefault('variables', {})
                if workflow.publication_policy == 'optional':
                    variables.setdefault('create_mr', False)
                if workflow.publication_policy == 'required' and str(variables.get('create_mr', True)).lower() in {'false','0','no'}:
                    raise ValueError('This workflow requires PR/MR publication')
                isolate = isolation == 'always' or isolation == 'auto' and any(r['sandbox'] != 'read-only' for r in settings['roles'].values())
            else:
                settings['effective_role'] = self.role(settings)
                isolate = isolation == 'always' or isolation == 'auto' and settings['effective_role']['sandbox'] != 'read-only'
            settings['isolate'] = isolate
            # Time-prefixed identities give task history a stable pagination cursor.
            tid = now() + '-' + uuid.uuid4().hex[:12]
            await self.store.write('INSERT INTO tasks (task_id,workspace_id,title,prompt,kind,settings_json,idempotency_key,fingerprint,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                                   (tid, workspace_id, prompt.splitlines()[0][:100], prompt, kind, json.dumps(settings), idempotency_key, fingerprint, now(), now()))
            self.schedule(tid, self.execute(tid))
            return await self.store.task(tid)

    async def execute(self, task_id, message=None):
        workspace = None
        leased = False
        lock = None
        acquired = False
        try:
            task = await self.store.task(task_id)
            await self.store.task_status(task_id, 'queued', 'Preparing workspace')
            workspace = await self.orch.workspaces.ensure_task_workspace(task_id, task['settings']['isolate'])
            lock = self.orch.workspaces.lock(workspace['workspace_id'])
            await lock.acquire()
            acquired = True
            latest = await self.store.workspace_record(workspace['workspace_id'])
            if latest['status'] != 'active':
                raise Conflict('Workspace is being archived')
            try:
                await self.store.write('INSERT INTO workspace_leases VALUES (?,?)', (workspace['workspace_id'], task_id))
            except Exception as exc:
                raise Conflict('Workspace is already executing another task') from exc
            leased = True
            await self.store.task_status(task_id, 'active', 'Starting agents')
            if task['kind'] == 'workflow':
                settings = task['settings']
                team = await self.orch.spawn_team(list(settings['roles']), task_id=task_id, configurations=settings['roles'])
                variables = {**settings.get('variables', {}), 'task_description': task['prompt'],
                             'cwd': workspace['path'], 'workspace_resolved': True, 'task_id': task_id, 'workspace_kind': workspace['kind']}
                run = WorkflowRunRecord(run_id=task.get("idempotency_key") or uuid.uuid4().hex, workflow_name=settings['workflow'], status='accepted', config={'variables': variables}, state={'team': team})
                await self.store.reserve_workflow_run(run)
                await self.store.write('UPDATE workflow_runs SET task_id=? WHERE run_id=?', (task_id, run.run_id))
                result = await self.orch.engine.run(self.orch.workflows[settings['workflow']], variables, run.run_id, accepted=True)
                await self.store.task_status(task_id, 'completed' if result.success else 'failed', 'Completed' if result.success else result.error or 'Workflow failed')
            else:
                agents = await self.store.rows('SELECT * FROM agents WHERE task_id=? ORDER BY created_at', (task_id,))
                if agents:
                    agent = await self.orch.registry.materialize(agents[0]['agent_id'])
                else:
                    agent = await self.orch.registry.create_agent(RoleConfig(**task['settings']['effective_role']), task_id=task_id)
                await self.orch.registry.start_agent(agent.agent_id)
                threads = await self.store.list_threads(agent.agent_id)
                if threads:
                    thread = await agent.resume_thread(threads[-1].thread_id)
                    thread.cwd = workspace['path']
                else:
                    thread = await agent.start_thread(cwd=workspace['path'])
                await self.store.task_status(task_id, 'active', 'Agent running')
                result = await agent.send_message(thread.thread_id, message if message is not None else task['prompt'])
                await self.store.task_status(task_id, 'active', 'Ready for your next message' if result.ok else result.error or 'Turn failed; you can retry')
        except asyncio.CancelledError:
            await self.store.task_status(task_id, 'interrupted', 'Execution interrupted; continue explicitly')
            raise
        except Exception as exc:
            await self.store.task_status(task_id, 'failed', str(exc))
        finally:
            try:
                for record in await self.store.rows('SELECT agent_id FROM agents WHERE task_id=?', (task_id,)):
                    if self.orch.registry.get_agent(record['agent_id']):
                        await self.orch.registry.stop_agent(record['agent_id'])
                if leased:
                    await self.store.write('DELETE FROM workspace_leases WHERE task_id=?', (task_id,))
            finally:
                if acquired:
                    lock.release()

    async def send_message(self, task_id, message):
        async with self.accept_lock:
            task = await self.store.task(task_id)
            if not task:
                raise LookupError('Task not found')
            workspace = await self.store.workspace_record(task['workspace_id'])
            if task['kind'] != 'agent' or task['status'] in TERMINAL or workspace['status'] != 'active':
                raise Conflict('Task cannot receive messages')
            if task_id in self.running:
                raise Conflict('A turn is already running')
            if not isinstance(message, str) or not message.strip():
                raise ValueError('A message is required')
            await self.store.task_status(task_id, 'queued', 'Waiting for workspace')
            self.schedule(task_id, self.execute(task_id, message.strip()))

    async def complete(self, task_id):
        async with self.accept_lock:
            task = await self.store.task(task_id)
            if not task:
                raise LookupError('Task not found')
            if task['kind'] != 'agent' or task_id in self.running or task['status'] in {'completed','failed'}:
                raise Conflict('Finish or cancel the active turn before completing the session')
            workspace = await self.store.workspace_record(task['workspace_id'])
            if workspace['status'] != 'active':
                raise Conflict('Workspace is unavailable')
            await self.store.task_status(task_id, 'completed', 'Manually completed')

    async def cancel(self, task_id):
        job = self.running.get(task_id)
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        await self.store.write("UPDATE input_requests SET status='expired',updated_at=? WHERE task_id=? AND status='pending'", (now(), task_id))
        await self.store.task_status(task_id, 'cancelled', 'Cancelled')

    async def request_input(self, task_id, prompt, kind='text', agent_id=None, execution_id=None, options=None, timeout=3600):
        rid = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.input_waiters[rid] = future
        try:
            await self.store.create_input(rid, task_id, prompt, kind, agent_id, execution_id, options)
            return await asyncio.wait_for(future, timeout)
        finally:
            self.input_waiters.pop(rid, None)
            await self.store.write("UPDATE input_requests SET status='expired',updated_at=? WHERE request_id=? AND status='pending'", (now(), rid))
            if task_id:
                await self.store.task_status(task_id, 'active', 'Continuing after input')

    async def resolve_input(self, request_id, response):
        future = self.input_waiters.get(request_id)
        if not future or future.done():
            raise Conflict('Input request is no longer active')
        if not await self.store.resolve_input(request_id, response):
            raise Conflict('Input request was already answered')
        future.set_result(response)

    async def shutdown(self):
        jobs = list(self.running.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
