"""Recovery actions executed only by the current task owner under its lease."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from orchestrator.state.models import now
from orchestrator.state.store import ThreadRecord
from orchestrator.roles.loader import RoleConfig
from orchestrator.workflows.merge_request import _run
from orchestrator.workflows.steps import AssignStep, ReviewStep, StepResult, task_snapshot


async def source_repair(service, ctx, decision, incident, attempt, expected_snapshot):
    root = Path(ctx.variables['task_worktree']).resolve()
    task = await ctx.store.task(ctx.variables['task_id'])
    workspace = await ctx.store.workspace_record(task['workspace_id'])
    lease = await ctx.store.one('SELECT * FROM workspace_leases WHERE workspace_id=? AND task_id=?', (workspace['workspace_id'], task['task_id']))
    if (root != Path(workspace['path']).resolve() or root != Path(ctx.variables['cwd']).resolve() or not lease
        or lease['owner_pid'] != service.orch.tasks.owner_pid or lease['owner_started'] != service.orch.tasks.owner_started):
        raise ValueError('Source repair requires ownership of the selected task checkout')
    stopped = []
    for member in list(service.orch.registry.agents.values()):
        if member.task_id == task['task_id'] and member.role.sandbox != 'read-only' and member.status.value != 'stopped':
            await service.orch.registry.stop_agent(member.agent_id)
            stopped.append(member)
    if await task_snapshot(ctx, require_clean=False) != expected_snapshot:
        raise ValueError('Checkout changed while stopping the previous writer; reassess retained work')
    paths = decision.get('repair_paths')
    if not isinstance(paths, list) or not 0 < len(paths) <= 64:
        raise ValueError('Source repair requires an explicit list of at most 64 files')
    for path in paths:
        if not isinstance(path, str) or Path(path).is_absolute() or not Path(path).parts:
            raise ValueError('Repair path is outside the selected checkout or is Git metadata')
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root) or (root / path).is_symlink():
            raise ValueError('Repair path is outside the selected checkout or is Git metadata')
        if '.git' in resolved.relative_to(root).parts:
            raise ValueError('Repair path is outside the selected checkout or is Git metadata')
    before = await task_snapshot(ctx)  # Never stage or commit pre-existing dirty files.
    if not before:
        raise ValueError('A selected task checkout is required for source repair')
    role = deepcopy(service.orch.roles.get('workflow-assistant') or service.orch.roles['implementer'])
    role.name, role.sandbox, role.max_turn_seconds, role.approval_policy = 'workflow-assistant', 'workspace-write', 180, 'never'
    role.system_prompt = (
        'Repair the assigned development blocker within the existing request and saved plan. '
        'Edit only the explicitly allowed paths in the selected checkout. Preserve all user changes. '
        'Do not change task scope or behavior, run tests/checks, install dependencies, modify Git metadata, '
        'stage, commit, switch branches, create worktrees, reset, stash, fetch, push, or publish. '
        'Use relevant installed skills. Service code will verify and commit the edits and require fresh source review. '
        'If intent is ambiguous, report the required decision without calling user-input tools. Return a report of edits, evidence and blockers.')
    agent = await service.orch.registry.create_agent(role, task_id=ctx.variables['task_id'])
    try:
        async with asyncio.timeout(180):
            await service.orch.registry.start_agent(agent.agent_id)
            thread = await agent.start_thread(cwd=str(root))
            await ctx.store.save_thread(ThreadRecord(thread_id=thread.thread_id, agent_id=agent.agent_id, cwd=str(root), model=role.model))
            await ctx.store.write('UPDATE threads SET task_id=? WHERE thread_id=?', (ctx.variables['task_id'], thread.thread_id))
            await ctx.store.write('UPDATE recovery_attempts SET agent_id=?,thread_id=? WHERE incident_id=? AND attempt=?',
                (agent.agent_id, thread.thread_id, incident, attempt))
            result = await agent.send_message(thread.thread_id, json.dumps({
                'request': ctx.variables['task_description'], 'plan': ctx.variables.get('approved_plan') or ctx.variables.get('plan'),
                'ux_plan': ctx.variables.get('ux_assessment'), 'allowed_paths': paths,
                'blocker': decision['summary'], 'instructions': decision['next_action'],
            }) + service.skill_instructions(role.name, {'cwd': str(root), 'bug_description': decision['summary']}))
    finally:
        await service.orch.registry.stop_agent(agent.agent_id)
    for path in paths:
        if (root / path).is_symlink() or not (root / path).resolve().is_relative_to(root):
            raise ValueError('Repair introduced a symlink or changed path ownership; edits retained')
    if await _run(str(root), 'git', 'rev-parse', 'HEAD') != before or await _run(str(root), 'git', 'branch', '--show-current') != ctx.variables['task_branch']:
        raise ValueError('Checkout identity changed during repair; edits retained for inspection')
    changed = set(filter(None, (await _run(str(root), 'git', 'diff', '--name-only', '-z', 'HEAD')).split('\0')))
    changed.update(filter(None, (await _run(str(root), 'git', 'ls-files', '--others', '--exclude-standard', '-z')).split('\0')))
    if not result.ok or not changed or changed - set(paths):
        raise ValueError('Repair was incomplete, made no progress, or edited unassigned files. Edits retained. ' + (result.error or result.full_text)[-4000:])
    tree = await service.orch.merges.change_tree(str(root))
    await _run(str(root), 'git', '--literal-pathspecs', 'add', '--all', '--', *sorted(changed))
    if await _run(str(root), 'git', 'write-tree') != tree or await service.orch.merges.change_tree(str(root)) != tree:
        raise ValueError('Files changed while staging the repair; staged edits retained for inspection')
    if (await _run(str(root), 'git', 'rev-parse', 'HEAD') != before
        or await _run(str(root), 'git', 'branch', '--show-current') != ctx.variables['task_branch']):
        raise ValueError('HEAD changed before the repair commit; staged edits retained')
    await _run(str(root), 'git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false', 'commit', '-m', 'Repair workflow blocker')
    commit = await task_snapshot(ctx)
    return result.full_text, commit, stopped


async def recover_step(service, ctx, workflow, step, failure):
    """Never replay an arbitrary step or grant a missing developer approval."""
    if ctx.variables.get('validation_stop'):
        # Final QA owns its own bounded repair policy and explicit continuation.
        await service.assess(ctx, step.step_id, failure.error or 'Validation stopped', {'boundary': 'validation_stop'})
        return failure
    snapshot = await task_snapshot(ctx, require_clean=False)
    decision = await service.assess(ctx, step.step_id, failure.error or 'Step failed',
        {'boundary': 'step_failure', 'snapshot': snapshot}, allow_repair=True)
    incident = decision['incident_id']
    row = await ctx.store.one('SELECT * FROM workflow_recoveries WHERE incident_id=?', (incident,))
    if row['attempts'] > 1 or row['status'] in {'recovered', 'replanning', 'awaiting_approval', 'superseded'}:
        return failure  # A saved repair is evidence, never permission to replay it.
    started = datetime.fromisoformat(row['created_at'])
    remaining = 600 - (datetime.now(timezone.utc) - started).total_seconds()
    review = next((s for s in workflow.steps if isinstance(s, ReviewStep) and s.role == 'reviewer' and not s.advisory), None)
    role_name = getattr(step, 'role', None)
    current_agent = ctx.get_agent_for_role(role_name) if role_name else None
    task = await ctx.store.task(ctx.variables['task_id']) if ctx.variables.get('task_id') else None
    configured = (task['settings'].get('roles') or {}).get(role_name) if task and role_name else None
    role_config = current_agent.role if current_agent else RoleConfig(**configured) if configured else service.orch.roles.get(role_name)
    safe_retry = (isinstance(step, (AssignStep, ReviewStep)) and role_name != 'implementer'
                  and role_config is not None and role_config.sandbox == 'read-only') or (
                      getattr(step, 'recovery_retry_safe', False) and ctx.variables.get('workspace_resolved') and ctx.variables.get('task_id'))
    result = failure
    try:
        async with asyncio.timeout(max(0, remaining)):
            for attempt in range(2, 4):
                if await task_snapshot(ctx, require_clean=False) != snapshot:
                    raise ValueError('Checkout changed after assessment; recovery must be assessed again')
                kind = decision['decision']
                if kind not in {'repair', 'review', 'retry', 'install', 'replan'}:
                    break
                if kind == 'replan':
                    gate = next((s for s in workflow.steps if s.__class__.__name__ == 'PlanApprovalStep'), None)
                    if not gate or 'plan' not in gate.targets:
                        raise ValueError('This workflow has no developer-approved technical replanning route')
                    plan = ctx.variables.get('plan_approval')
                    if not plan:
                        raise ValueError('No saved plan is available to refresh')
                    plan.update(status='rerouting', error=None)
                    ctx.variables.update(approved_plan='', planning_feedback=decision['summary'] + '\n' + decision['next_action']
                        + '\nCurrent developer draft:\n' + plan['markdown'], planning_targets=list(gate.targets))
                    result = StepResult(True, next_step='plan')
                    break
                if kind == 'retry' and not safe_retry:
                    raise ValueError('The failed operation may have side effects; inspect its effects before an explicit Continue')
                if kind in {'repair', 'review'}:
                    if review is None or not ctx.variables.get('task_worktree'):
                        raise ValueError('Source repair requires a selected checkout and a reviewer route')
                    if (ctx.variables.get('_requires_plan_approval') or any(s.__class__.__name__ == 'PlanApprovalStep' for s in workflow.steps)) and (ctx.variables.get('plan_approval') or {}).get('status') != 'approved':
                        raise ValueError('Developer approval is required before any source repair')
                await ctx.store.write('INSERT INTO recovery_attempts (incident_id,attempt,status,action,started_at) VALUES (?,?,?,?,?)',
                    (incident, attempt, 'working', kind, now()))
                await ctx.store.write("UPDATE workflow_recoveries SET attempts=?,status='working',updated_at=? WHERE incident_id=?", (attempt, now(), incident))
                report = ''
                try:
                    if kind == 'install':
                        ready, report = await service.install_capability(incident, decision.get('install_request'), attempt)
                        if not ready:
                            raise ValueError(report)
                        ctx.variables['_recovery_capabilities'] = report
                        service.activate_capabilities(report)
                        decision['decision'] = 'retry'
                    elif kind == 'repair':
                        report, commit, stopped = await source_repair(service, ctx, decision, incident, attempt, snapshot)
                        decision['repair_commit'] = commit
                        ctx.variables.setdefault('_recovery_prior_evidence', []).append({key: value for key, value in ctx.variables.items() if key.startswith(('review_', 'validation_'))})
                        for key in list(ctx.variables):
                            if key.startswith(('review_', 'validation_')) or key == 'completion_reviewed_sha':
                                ctx.variables.pop(key, None)
                        ctx.variables.update(implementation=ctx.variables.get('implementation', '') + '\nWorkflow repair:\n' + report,
                                             _recovery_review_commit=commit, task_commit=commit)
                        ctx.variables[review.source_key] = ctx.variables['implementation']
                        if ctx.variables.get('_recovery_pre_failure_mr_status') == 'prepared':
                            ctx.variables['mr_status'] = 'prepared'
                        result = StepResult(True, next_step=review.step_id)
                        for member in stopped:
                            ctx._attached = {key for key in ctx._attached if not key.startswith(member.role.name + ':')}
                            await service.orch.registry.start_agent(member.agent_id)
                    elif kind == 'review':
                        commit = await task_snapshot(ctx)
                        if not commit:
                            raise ValueError('Fresh review requires a selected clean checkout')
                        for key in list(ctx.variables):
                            if key.startswith('review_') or key == 'completion_reviewed_sha':
                                ctx.variables.pop(key, None)
                        ctx.variables.update(task_commit=commit, _recovery_review_commit=commit)
                        decision['repair_commit'] = commit
                        report = 'Existing commit retained; fresh source review requested at ' + commit
                        if ctx.variables.get('_recovery_pre_failure_mr_status') == 'prepared':
                            ctx.variables['mr_status'] = 'prepared'
                        result = StepResult(True, next_step=review.step_id)
                    else:
                        if role_name and not ctx.get_agent_for_role(role_name):
                            if role_config is None or role_config.sandbox != 'read-only':
                                raise ValueError('A missing writable agent requires the normal implementation path')
                            members = service.orch.registry.get_agents_by_role(role_name, ctx.variables.get('task_id'))
                            agent = members[0] if members else await service.orch.registry.create_agent(deepcopy(role_config), task_id=ctx.variables.get('task_id'))
                            await service.orch.registry.start_agent(agent.agent_id)
                            ctx._attached = {key for key in ctx._attached if not key.startswith(role_name + ':')}
                        before = await task_snapshot(ctx, require_clean=False)
                        result = await ctx.execute_step(step)
                        if result.success and result.next_step and result.next_step != '__end__' and workflow.get_step(result.next_step) is None:
                            result = StepResult(False, output=result.output, error='Retry returned an unknown branch target: ' + result.next_step)
                        report = json.dumps({'success': result.success, 'error': result.error, 'output': result.output})
                        if await task_snapshot(ctx, require_clean=False) != before:
                            raise ValueError('Read-only retry changed the checkout; inspect retained changes')
                    await ctx.store.write('UPDATE recovery_attempts SET status=?,report=?,finished_at=? WHERE incident_id=? AND attempt=?',
                        ('completed' if kind == 'install' or result.success else 'failed', report, now(), incident, attempt))
                except BaseException as exc:
                    await ctx.store.write('UPDATE recovery_attempts SET status=?,report=?,finished_at=? WHERE incident_id=? AND attempt=?',
                        ('interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed', str(exc), now(), incident, attempt))
                    raise
                if result.success or kind != 'install':
                    break  # No repeated identical retries or repair loops.
    except asyncio.CancelledError:
        decision.update(decision='input', summary='Recovery interrupted; inspect retained work before continuing.')
        raise
    except (ValueError, OSError, RuntimeError, TimeoutError, KeyError, TypeError) as exc:
        result = failure
        decision.update(decision='input', summary=str(exc), next_action='Inspect the saved attempts and retained checkout changes, then retry recovery or Continue.')
    finally:
        decision.update(incident_id=incident)
        status = ('repair_applied' if decision.get('repair_commit') else 'replanning' if decision['decision'] == 'replan' else 'recovered') if result.success else 'needs_input'
        if result.success:
            decision['next_action'] = 'Continue from ' + str(result.next_step or step.step_id) + '; required approval and validation gates still apply.'
        await ctx.store.write('UPDATE workflow_recoveries SET status=?,result_json=?,updated_at=? WHERE incident_id=?',
            (status, json.dumps(decision), now(), incident))
        await ctx.persist()
    return result
