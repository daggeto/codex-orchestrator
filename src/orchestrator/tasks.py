"""Service-owned workflow and conversational task lifecycle."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import uuid

from orchestrator.processes import owner_alive, process_identity
from orchestrator.workflows.merge_request import _run, resolve_publication_identity
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
        self.input_handlers = {}
        self.cancelling = set()
        self.owner_pid = os.getpid()
        self.owner_started = process_identity(self.owner_pid)

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
        if any(key.startswith('legacy_') for key in settings):
            raise ValueError('Legacy sessions use saved-agent commands')
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
                raise Conflict('Workspace is merging, awaiting merge recovery, being archived, or unavailable')
            if kind == 'workflow':
                workflow = self.orch.workflows.get(settings.get('workflow', 'small-change'))
                if not workflow:
                    raise ValueError('Unknown workflow')
                settings['workflow'] = workflow.name
                settings['roles'] = {name: self.role({'role': name}) for name in workflow.required_roles}
                for name, overrides in workflow.role_overrides.items():
                    role = settings['roles'][name]
                    role.update({key: value for key, value in overrides.items() if key != 'config_overrides'})
                    role['config_overrides'].update(overrides.get('config_overrides', {}))
                variables = settings.setdefault('variables', {})
                if workflow.name == 'small-change' and str(variables.get('create_mr', False)).lower() not in {'false', '0', 'no'}:
                    raise ValueError('small-change never publishes; select feature for PR/MR publication')
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
            claimed = await self.store.write("INSERT INTO tasks (task_id,workspace_id,title,prompt,kind,settings_json,idempotency_key,fingerprint,created_at,updated_at) SELECT ?,?,?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM workspaces WHERE workspace_id=? AND status='active')",
                                   (tid, workspace_id, prompt.splitlines()[0][:100], prompt, kind, json.dumps(settings), idempotency_key, fingerprint, now(), now(), workspace_id))
            if not claimed:
                raise Conflict('Workspace is reserved for merging, recovery, or archiving')
            await self.store.write('UPDATE tasks SET owner_pid=?,owner_started=? WHERE task_id=?',
                                   (self.owner_pid, self.owner_started, tid))
            self.schedule(tid, self.execute(tid))
            return await self.store.task(tid)

    async def run_legacy(self, agent, message, operation, cwd=None, thread_id=None, supervised=False):
        if not isinstance(message, str) or not message.strip():
            raise ValueError('A message is required')
        if getattr(agent, 'task_id', None):
            raise Conflict('Use task messaging for a task-owned agent')
        if thread_id:
            saved = await self.store.get_thread(thread_id)
            runtime = agent._threads.get(thread_id)
            if saved and saved.agent_id != agent.agent_id:
                raise Conflict('Thread belongs to another agent')
            thread_cwd = saved.cwd if saved else runtime.cwd if runtime else None
            if not thread_cwd:
                raise Conflict('Saved thread has no verifiable workspace')
            if cwd and Path(cwd).resolve() != Path(thread_cwd).resolve():
                raise Conflict('Saved thread belongs to a different directory')
            cwd = thread_cwd
        root = await _run(str(Path(cwd or self.orch.config.effective_project_dir).resolve()),
                          'git', 'rev-parse', '--show-toplevel')
        await self.orch.workspaces.register(root)
        async with self.accept_lock:
            workspace = await self.store.one('SELECT * FROM workspaces WHERE path=?', (root,))
            if not workspace or workspace['status'] != 'active':
                raise Conflict('Workspace is merging, awaiting merge recovery, being archived, or unavailable')
            tid = now() + '-' + uuid.uuid4().hex[:12]
            claimed = await self.store.write(
                "INSERT INTO tasks (task_id,workspace_id,title,prompt,kind,settings_json,fingerprint,created_at,updated_at,owner_pid,owner_started) SELECT ?,?,?,?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM workspaces WHERE workspace_id=? AND status='active')",
                (tid, workspace['workspace_id'], message.splitlines()[0][:100], message, 'agent',
                 json.dumps({'legacy_agent_id': agent.agent_id, 'legacy_thread_id': thread_id,
                             'legacy_supervised': supervised, 'legacy_cwd': cwd or root,
                             'isolate': False, 'effective_role': asdict(agent.role)}),
                 '', now(), now(), self.owner_pid, self.owner_started, workspace['workspace_id']))
            if not claimed:
                raise Conflict('Workspace is reserved for merging, recovery, or archiving')
            await self.store.participate(tid, agent.agent_id)
            job = self.schedule(tid, self._execute_legacy(tid, workspace, agent, operation, cwd or root))
        return await asyncio.shield(job)

    async def _execute_legacy(self, task_id, workspace, agent, operation, cwd):
        async with self.orch.workspaces.lock(workspace['workspace_id']):
            leased = False
            started = False
            try:
                task = await self.store.task(task_id)
                if task['status'] in TERMINAL:
                    raise Conflict('Task was cancelled before execution')
                await self.orch.workspaces.claim_lease(workspace['workspace_id'], task_id, self.owner_pid, self.owner_started)
                leased = True
                if getattr(agent, 'task_id', None):
                    raise Conflict('Agent is already executing another task')
                agent.task_id = task_id
                started = True
                await self.orch.registry.start_agent(agent.agent_id)
                if not await self.store.task_status(task_id, 'active', 'Agent running', expected=('queued',)):
                    raise Conflict('Task changed before execution')
                await self.store.write('UPDATE tasks SET last_turn_json=NULL WHERE task_id=?', (task_id,))
                result = await operation(cwd)
                await self.store.task_status(task_id, 'completed', 'Completed', expected=('active', 'waiting_input'))
                return result
            except asyncio.CancelledError:
                await self.store.task_status(task_id, 'interrupted', 'Execution interrupted', expected=('queued','active','waiting_input'))
                raise
            except Exception as exc:
                await self.store.task_status(task_id, 'failed', str(exc), expected=('queued','active','waiting_input'))
                raise
            finally:
                try:
                    if started:
                        await self.orch.registry.stop_agent(agent.agent_id)
                    if leased:
                        await self.store.write('DELETE FROM workspace_leases WHERE task_id=?', (task_id,))
                finally:
                    if started:
                        agent.task_id = None

    async def execute(self, task_id, message=None):
        workspace = None
        owned = False
        leased = False
        lock = None
        acquired = False
        try:
            task = await self.store.task(task_id)
            if task_id in self.cancelling or task['status'] in TERMINAL:
                return
            if task['owner_pid'] and task['owner_pid'] != self.owner_pid and owner_alive(task['owner_pid'], task['owner_started']):
                return
            claimed = await self.store.write('UPDATE tasks SET owner_pid=?,owner_started=? WHERE task_id=? AND owner_pid IS ? AND owner_started IS ?',
                                   (self.owner_pid, self.owner_started, task_id, task['owner_pid'], task['owner_started']))
            if not claimed:
                return
            owned = True
            await self.store.task_status(task_id, 'queued', 'Preparing workspace', expected=('queued','active','interrupted'))
            settings = task['settings']
            if settings.get('legacy_agent_id'):
                agent = await self.orch.registry.materialize(settings['legacy_agent_id'])
                workspace = await self.store.workspace_record(task['workspace_id'])
                thread_id = settings.get('legacy_thread_id')
                if not thread_id:
                    thread = await self.store.one('SELECT t.* FROM threads t JOIN task_threads p USING(thread_id) WHERE p.task_id=? AND t.agent_id=? ORDER BY t.updated_at DESC LIMIT 1', (task_id, agent.agent_id))
                    thread_id = thread['thread_id'] if thread else None
                else:
                    thread = await self.store.get_thread(thread_id)
                    thread = {'cwd': thread.cwd} if thread else None
                cwd = settings.get('legacy_cwd') or (thread or {}).get('cwd') or workspace['path']
                operation = self.orch._legacy_operation(agent, message if message is not None else task['prompt'], thread_id,
                                                        settings.get('legacy_supervised', False))
                await self._execute_legacy(task_id, workspace, agent, operation, cwd)
                return
            await self.store.write('UPDATE tasks SET last_turn_json=NULL WHERE task_id=?', (task_id,))
            if task['kind'] == 'workflow' and str(settings.get('variables', {}).get('create_mr', True)).lower() not in {'false','0','no'}:
                workflow = self.orch.workflows[settings['workflow']]
                if workflow.publication_policy in {'required', 'optional'}:
                    source = await self.store.workspace_record(task['workspace_id'])
                    variables = settings.setdefault('variables', {})
                    if not variables.get('publication_source'):
                        variables['publication_source'] = await resolve_publication_identity(source['path'], variables)
                        await self.store.write('UPDATE tasks SET settings_json=? WHERE task_id=?', (json.dumps(settings), task_id))
            workspace = await self.orch.workspaces.ensure_task_workspace(task_id, task['settings']['isolate'])
            lock = self.orch.workspaces.lock(workspace['workspace_id'])
            await lock.acquire()
            acquired = True
            latest = await self.store.workspace_record(workspace['workspace_id'])
            if latest['status'] != 'active':
                raise Conflict('Workspace is merging, awaiting merge recovery, or being archived')
            await self.orch.workspaces.claim_lease(workspace['workspace_id'], task_id, self.owner_pid, self.owner_started)
            leased = True
            await self.store.task_status(task_id, 'active', 'Starting agents', expected=('queued',))
            if task['kind'] == 'workflow':
                settings = task['settings']
                team = await self.orch.spawn_team(list(settings['roles']), task_id=task_id, configurations=settings['roles'])
                variables = {**settings.get('variables', {}), 'task_description': task['prompt'],
                             'cwd': workspace['path'], 'workspace_resolved': True, 'task_id': task_id, 'workspace_kind': workspace['kind']}
                run = WorkflowRunRecord(run_id=task.get("idempotency_key") or uuid.uuid4().hex, workflow_name=settings['workflow'], status='accepted', config={'variables': variables}, state={'team': team})
                await self.store.reserve_workflow_run(run)
                await self.store.write('UPDATE workflow_runs SET task_id=? WHERE run_id=?', (task_id, run.run_id))
                result = await self.orch.engine.run(self.orch.workflows[settings['workflow']], variables, run.run_id, accepted=True)
                await self.store.task_status(task_id, 'completed' if result.success else 'failed', 'Completed' if result.success else result.error or 'Workflow failed', expected=('active','waiting_input'))
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
                await self.store.task_status(task_id, 'active', 'Agent running', expected=('queued','active'))
                result = await agent.send_message(thread.thread_id, message if message is not None else task['prompt'])
                await self.store.record_turn(task_id, result)
                await self.store.task_status(task_id, 'active', 'Ready for your next message' if result.ok else result.error or 'Turn failed; you can retry', expected=('active','waiting_input'))
        except asyncio.CancelledError:
            if owned and task_id not in self.cancelling:
                await self.store.task_status(task_id, 'interrupted', 'Execution interrupted; continue explicitly', expected=('queued','active','waiting_input'))
            raise
        except Exception as exc:
            if owned:
                await self.store.task_status(task_id, 'failed', str(exc), expected=('queued','active','waiting_input'))
        finally:
            try:
                for record in (await self.store.rows('SELECT agent_id FROM agents WHERE task_id=?', (task_id,)) if owned else []):
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
            if workspace['status'] in {'merging', 'merge_recovery_required'}:
                raise Conflict('Workspace is reserved for merging or merge recovery; continue after it is released')
            if task['kind'] != 'agent' or task['status'] in TERMINAL or workspace['status'] != 'active':
                raise Conflict('Task cannot receive messages')
            if task['owner_pid'] and task['owner_pid'] != self.owner_pid and owner_alive(task['owner_pid'], task['owner_started']):
                raise Conflict('Task belongs to another live service')
            if task_id in self.running:
                raise Conflict('A turn is already running')
            if not isinstance(message, str) or not message.strip():
                raise ValueError('A message is required')
            if not await self.store.task_status(task_id, 'queued', 'Waiting for workspace', expected=('active', 'interrupted')):
                raise Conflict('Task changed while accepting the message')
            self.schedule(task_id, self.execute(task_id, message.strip()))

    async def set_archived(self, task_id, archived):
        if not await self.store.task(task_id):
            raise LookupError('Task not found')
        if not await self.store.set_task_archived(task_id, archived):
            raise Conflict('Complete or cancel this task before archiving it')
        return await self.store.task(task_id)

    async def complete(self, task_id):
        async with self.accept_lock:
            task = await self.store.task(task_id)
            if not task:
                raise LookupError('Task not found')
            if task['owner_pid'] and task['owner_pid'] != self.owner_pid and owner_alive(task['owner_pid'], task['owner_started']):
                raise Conflict('Task belongs to another live service')
            if task['kind'] != 'agent' or task_id in self.running or task['status'] in {'completed','failed'}:
                raise Conflict('Finish or cancel the active turn before completing the session')
            workspace = await self.store.workspace_record(task['workspace_id'])
            if workspace['status'] != 'active':
                raise Conflict('Workspace is merging, awaiting merge recovery, or unavailable')
            await self.store.task_status(task_id, 'completed', 'Manually completed')

    async def cancel(self, task_id):
        async with self.accept_lock:
            task = await self.store.task(task_id)
            if task and task['owner_pid'] and task['owner_pid'] != self.owner_pid and owner_alive(task['owner_pid'], task['owner_started']):
                raise Conflict('Task belongs to another live service; cancel it through that service')
            self.cancelling.add(task_id)
            try:
                await self.store.task_status(task_id, 'cancelled', 'Cancelled')
                job = self.running.get(task_id)
                if job:
                    job.cancel()
                    await asyncio.gather(job, return_exceptions=True)
                handlers = list(self.input_handlers.get(task_id, set()))
                for handler in handlers:
                    handler.cancel()
                await asyncio.gather(*handlers, return_exceptions=True)
                await self.store.write("UPDATE input_requests SET status='expired',updated_at=? WHERE task_id=? AND status='pending'", (now(), task_id))
                await self.store.task_status(task_id, 'cancelled', 'Cancelled')
            finally:
                self.cancelling.discard(task_id)

    async def request_input(self, task_id, prompt, kind='text', agent_id=None, execution_id=None, options=None, timeout=3600):
        if task_id:
            task = await self.store.task(task_id)
            if task_id in self.cancelling or not task or task['status'] in TERMINAL:
                raise asyncio.CancelledError
        handler = asyncio.current_task()
        self.input_handlers.setdefault(task_id, set()).add(handler)
        rid = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.input_waiters[rid] = future
        try:
            await self.store.create_input(rid, task_id, prompt, kind, agent_id, execution_id, options)
            if task_id:
                task = await self.store.task(task_id)
                if task_id in self.cancelling or not task or task['status'] in TERMINAL:
                    raise asyncio.CancelledError
            return await asyncio.wait_for(future, timeout)
        finally:
            self.input_waiters.pop(rid, None)
            self.input_handlers.get(task_id, set()).discard(handler)
            await self.store.write("UPDATE input_requests SET status='expired',updated_at=? WHERE request_id=? AND status='pending'", (now(), rid))
            if task_id:
                await self.store.task_status(task_id, 'active', 'Continuing after input', expected=('waiting_input',))

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
        handlers = {handler for group in self.input_handlers.values() for handler in group}
        for handler in handlers:
            handler.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
