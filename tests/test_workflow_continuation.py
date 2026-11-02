"""Continuation uses the owning task's checkout, agents, and execution lease."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from orchestrator.agents.base import AgentStatus
from orchestrator.api.submissions import accept_continuation, get_run
from orchestrator.events.bus import EventBus
from orchestrator.roles.loader import RoleConfig
from orchestrator.state.store import AgentRecord, StateStore, WorkflowRunRecord
from orchestrator.tasks import Conflict, TaskService
from orchestrator.workspaces import WorkspaceService
from orchestrator.workflows.engine import Workflow, WorkflowEngine
from orchestrator.workflows.builtins import ui_small_workflow
from orchestrator.workflows.steps import Step, StepResult


class ProbeStep(Step):
    __slots__ = ('probe',)  # Keep test callbacks out of the serialized definition.
    role = 'writer'

    def __init__(self, step_id, probe):
        super().__init__(step_id)
        self.probe = probe

    async def execute(self, ctx):
        await self.probe(ctx)
        return StepResult(success=True)


class WorkflowContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = str(Path(directory.name).resolve())
        self.store = StateStore(Path(self.root) / 'state.db')
        await self.store.init()
        self.addAsyncCleanup(self.store.close)
        await self.store.write("INSERT INTO repositories VALUES ('repo','Repo',?,'git',1,'now','now')", (self.root,))
        await self.store.write("INSERT INTO workspaces VALUES ('workspace','repo','worktree',?,'feature','sha','active','now','now')", (self.root,))
        self.role = RoleConfig(name='writer', model='saved-model', config_overrides={'model_reasoning_effort': 'high'})
        settings = {'workflow': 'example', 'roles': {'writer': asdict(self.role)}, 'isolate': True}
        await self.store.write("INSERT INTO tasks (task_id,workspace_id,title,prompt,kind,settings_json,status,fingerprint,created_at,updated_at) VALUES ('task','workspace','Work','Work','workflow',?,'failed','','now','now')", (json.dumps(settings),))
        await self.store.save_agent(AgentRecord('agent', 'writer', 'codex', config=asdict(self.role)))
        await self.store.write("UPDATE agents SET task_id='task' WHERE agent_id='agent'")
        self.agent = SimpleNamespace(agent_id='agent', role=self.role, status=AgentStatus.RUNNING)
        registry = SimpleNamespace(get_agents_by_role=Mock(side_effect=lambda role, task_id: [self.agent] if (role, task_id) == ('writer', 'task') else []),
                                   get_agent=Mock(return_value=self.agent), stop_agent=AsyncMock())
        self.first = AsyncMock()
        self.resumed = AsyncMock(side_effect=self.check_resumed_execution)
        self.workflow = Workflow('example', steps=[ProbeStep('first', self.first), ProbeStep('resume', self.resumed)])
        self.orch = SimpleNamespace(store=self.store, registry=registry, workflows={'example': self.workflow},
                                    spawn_team=AsyncMock(return_value={'writer': 'agent'}))
        self.orch.engine = WorkflowEngine(registry, self.store, EventBus())
        self.orch.workspaces = WorkspaceService(self.orch)
        self.orch.workspaces.ensure_task_workspace = AsyncMock()
        self.orch.tasks = TaskService(self.orch)
        self.addAsyncCleanup(self.orch.tasks.shutdown)
        self.record = WorkflowRunRecord(run_id='run', workflow_name='example', status='failed',
            config={'variables': {'cwd': self.root}, 'workflow_signature': self.workflow.signature},
            state={'checkpoint': {'next_step': 'resume', 'iteration': 1,
                   'variables': {'cwd': self.root, 'task_id': 'task', 'saved_output': 'keep me'},
                   'threads': {'writer:codex:resume': 'saved-thread'}}, 'error': 'Stopped'})
        await self.store.save_workflow_run(self.record)
        await self.store.write("UPDATE workflow_runs SET task_id='task' WHERE run_id='run'")
        self.request = SimpleNamespace(app={'orch': self.orch, 'submitted_tasks': set()},
                                       match_info={'run_id': 'run'}, json=AsyncMock(return_value={'request_id': 'retry'}))

    async def check_resumed_execution(self, ctx):
        lease = await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='workspace'")
        self.assertEqual('task', lease['task_id'])
        self.assertEqual(self.orch.tasks.owner_pid, lease['owner_pid'])
        self.assertEqual('task', ctx.variables['task_id'])
        self.assertEqual(self.root, ctx.variables['cwd'])
        self.assertEqual('keep me', ctx.variables['saved_output'])
        self.assertEqual('saved-thread', ctx._threads['writer:codex:resume'])
        self.assertIs(self.agent, ctx.get_agent_for_role('writer'))

    async def finish_job(self):
        jobs = list(self.orch.tasks.running.values())
        self.assertEqual(1, len(jobs))
        await jobs[0]

    async def test_merge_reservations_reject_before_run_or_agents_change_then_resume(self):
        before = await self.store.get_workflow_run('run')
        for status in ('merging', 'merge_recovery_required'):
            with self.subTest(status=status):
                await self.store.write("UPDATE workspaces SET status=? WHERE workspace_id='workspace'", (status,))
                response = await accept_continuation(self.request)
                self.assertEqual(409, response.status)
                self.assertIn('reserved for merging or merge recovery', json.loads(response.text)['error'])
                self.assertEqual(before, await self.store.get_workflow_run('run'))
                self.assertEqual('failed', (await self.store.task('task'))['status'])
                self.assertEqual({}, self.orch.tasks.running)
                self.orch.spawn_team.assert_not_awaited()
        await self.store.write("UPDATE workspaces SET status='active' WHERE workspace_id='workspace'")
        self.assertEqual(202, (await accept_continuation(self.request)).status)
        await self.finish_job()
        self.first.assert_not_awaited()
        self.resumed.assert_awaited_once()
        self.orch.spawn_team.assert_awaited_once_with(['writer'], task_id='task', configurations={'writer': asdict(self.role)})
        self.orch.workspaces.ensure_task_workspace.assert_not_awaited()
        self.assertEqual('completed', (await self.store.task('task'))['status'])
        self.assertEqual('completed', (await self.store.get_workflow_run('run')).status)
        self.assertIsNone(await self.store.one("SELECT * FROM workspace_leases WHERE workspace_id='workspace'"))

    async def test_existing_workspace_lease_rejects_without_claiming_run(self):
        await self.orch.workspaces.claim_lease('workspace', 'task', self.orch.tasks.owner_pid, self.orch.tasks.owner_started)
        before = await self.store.get_workflow_run('run')
        self.assertEqual(409, (await accept_continuation(self.request)).status)
        self.assertEqual(before, await self.store.get_workflow_run('run'))
        self.orch.spawn_team.assert_not_awaited()

    async def test_changed_definition_continues_saved_step_with_current_routing(self):
        after = AsyncMock()
        self.workflow.add_step(ProbeStep('after', after))
        self.assertNotEqual(self.record.config['workflow_signature'], self.workflow.signature)
        info = json.loads((await get_run(self.request)).text)
        self.assertTrue(info['continuation']['available'])
        self.assertEqual(202, (await accept_continuation(self.request)).status)
        await self.finish_job()
        self.first.assert_not_awaited()
        self.resumed.assert_awaited_once()
        after.assert_awaited_once()
        record = await self.store.get_workflow_run('run')
        self.assertEqual('completed', record.status)
        self.assertEqual(self.workflow.signature, record.config['workflow_signature'])

    async def test_removed_saved_step_still_rejects_before_claiming_run(self):
        self.workflow.steps = [self.workflow.steps[0]]
        before = await self.store.get_workflow_run('run')
        info = json.loads((await get_run(self.request)).text)
        self.assertFalse(info['continuation']['available'])
        response = await accept_continuation(self.request)
        self.assertEqual(400, response.status)
        self.assertIn('saved step is unavailable', json.loads(response.text)['error'])
        self.assertEqual(before, await self.store.get_workflow_run('run'))
        self.orch.spawn_team.assert_not_awaited()

    async def test_reservation_racing_with_acceptance_cannot_change_run(self):
        before = await self.store.get_workflow_run('run')
        claim = self.store.claim_workflow_continuation
        async def reserve_before_claim(record):
            await self.store.write("UPDATE workspaces SET status='merging' WHERE workspace_id='workspace'")
            return await claim(record)
        with patch.object(self.store, 'claim_workflow_continuation', side_effect=reserve_before_claim):
            response = await accept_continuation(self.request)
        self.assertEqual(409, response.status)
        self.assertIn('reserved for merging or merge recovery', json.loads(response.text)['error'])
        self.assertEqual(before, await self.store.get_workflow_run('run'))
        self.orch.spawn_team.assert_not_awaited()

    async def test_duplicate_continuation_does_not_start_another_team(self):
        self.assertEqual(202, (await accept_continuation(self.request)).status)
        self.assertEqual(202, (await accept_continuation(self.request)).status)
        await self.finish_job()
        self.orch.spawn_team.assert_awaited_once()

    async def test_old_review_gate_previews_and_accepts_fresh_review_with_budget_extension(self):
        workflow = ui_small_workflow()
        self.orch.workflows['ui-small'] = workflow
        self.record.workflow_name = 'ui-small'
        self.record.status = 'paused'
        self.record.config['iteration_limit'] = 40
        self.record.state['checkpoint'].update(next_step='review-gate', iteration=40)
        self.record.state['checkpoint']['variables'].update(implementation='Saved work', review_decision='approved')
        await self.store.save_workflow_run(self.record)
        info = json.loads((await get_run(self.request)).text)['continuation']
        self.assertTrue(info['available'])
        self.assertTrue(info['review_refresh_required'])
        self.assertTrue(info['requires_extension'])
        self.assertEqual(info['step'], 'review')
        self.assertEqual(409, (await accept_continuation(self.request)).status)
        self.request.json.return_value = {'request_id': 'retry', 'additional_steps': 20}
        with patch.object(self.orch.tasks, 'schedule') as schedule:
            self.assertEqual(202, (await accept_continuation(self.request)).status)
        schedule.call_args.args[1].close()
        saved = await self.store.get_workflow_run('run')
        self.assertEqual(saved.config['iteration_limit'], 60)
        self.assertEqual(saved.state['checkpoint']['iteration'], 40)
        self.assertEqual(saved.state['checkpoint']['next_step'], 'review')
        self.assertEqual(saved.state['checkpoint']['variables']['implementation'], 'Saved work')
        self.assertNotIn('review_decision', saved.state['checkpoint']['variables'])
        self.assertEqual(saved.state['checkpoint']['variables']['validation_resume_request'], 'retry')
        self.orch.spawn_team.assert_not_awaited()

    async def test_accepted_continuation_blocks_archive_before_execution_starts(self):
        with patch.object(self.orch.tasks, 'schedule') as schedule:
            self.assertEqual(202, (await accept_continuation(self.request)).status)
        execution = schedule.call_args.args[1]
        self.addCleanup(execution.close)
        self.orch.spawn_team.assert_not_awaited()
        self.assertEqual('accepted', (await self.store.get_workflow_run('run')).status)
        self.assertEqual('queued', (await self.store.task('task'))['status'])
        with self.assertRaises(Conflict):
            await self.orch.tasks.set_archived('task', True)
        await execution
        self.resumed.assert_awaited_once()
        self.assertIsNone((await self.store.task('task'))['archived_at'])

    async def test_archive_winning_before_continuation_claim_leaves_run_stopped(self):
        before = await self.store.get_workflow_run('run')
        claim = self.store.claim_workflow_continuation
        async def archive_before_claim(record):
            await self.orch.tasks.set_archived('task', True)
            return await claim(record)
        with patch.object(self.store, 'claim_workflow_continuation', side_effect=archive_before_claim):
            self.assertEqual(409, (await accept_continuation(self.request)).status)
        self.assertEqual(before, await self.store.get_workflow_run('run'))
        self.assertIsNotNone((await self.store.task('task'))['archived_at'])
        self.assertEqual('failed', (await self.store.task('task'))['status'])
        self.orch.spawn_team.assert_not_awaited()

    async def test_unowned_legacy_run_cannot_bypass_task_guards(self):
        await self.store.write("UPDATE workflow_runs SET task_id=NULL WHERE run_id='run'")
        before = await self.store.get_workflow_run('run')
        response = await accept_continuation(self.request)
        self.assertEqual(409, response.status)
        self.assertIn('no owning task', json.loads(response.text)['error'])
        self.assertEqual(before, await self.store.get_workflow_run('run'))
        self.assertFalse(json.loads((await get_run(self.request)).text)['continuation']['available'])
        self.orch.spawn_team.assert_not_awaited()


class SharedAttachmentContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_role_and_resumed_turn_resolves_current_task_resources(self):
        from orchestrator.agents.codex import CodexAgent
        from orchestrator.roles.loader import RoleConfig
        resources = [{'attachment_id': 'file', 'filename': 'notes.txt', 'path': '/private/file',
                      'media_type': 'application/octet-stream', 'byte_count': 10, 'width': None}]
        for role in ('implementer', 'reviewer', 'senior-ux-designer'):
            agent = CodexAgent(role, RoleConfig(name=role, sandbox='read-only'), 9001)
            agent.task_id = 'task'
            agent.attachment_resolver = AsyncMock(return_value=list(resources))
            first = await agent.prepare_input('Assigned work')
            self.assertEqual(resources, first.attachments)
            current = resources + [{**resources[0], 'attachment_id': 'new'}]
            agent.attachment_resolver.return_value = current
            resumed = await agent.prepare_input('Continue')
            self.assertEqual(current, resumed.attachments)
            self.assertEqual('read-only', agent.role.sandbox)
            self.assertEqual(2, agent.attachment_resolver.await_count)
