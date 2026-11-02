"""Commit-bound QA and bounded repairs with durable recovery guidance."""
from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

from orchestrator.workflows.steps import AssignStep, Step, StepResult
from orchestrator.workflows.merge_request import _run
from orchestrator.workflows.small_change import MAX_SOURCE_REVISIONS, source_revision_failure

DEFER_CHECKS = (
    "\n\nWorkflow validation policy: source stages must not run tests, typecheck, or lint. "
    "QA owns final automated checks after source approvals. Follow repository restrictions. "
    "Report pending browser acceptance separately."
)
MAX_REPAIRS = 3


def validation_report(text):
    """Read the final JSON report even when streamed commentary precedes it."""
    decoder = json.JSONDecoder()
    reports = []
    for offset, character in enumerate(text):
        if character != '{':
            continue
        try:
            value, end = decoder.raw_decode(text, offset)
        except ValueError:
            continue
        if (isinstance(value, dict) and 'checks' in value
                and text[end:].strip() in ('', '```')):
            reports.append(value)
    if len(reports) != 1:
        raise ValueError('QA must provide one final JSON validation report')
    return reports[0]


class FinalValidationStep(Step):
    def __init__(self, reviews, consultations=None, **kwargs):
        super().__init__(**kwargs)
        self.reviews = reviews
        self.consultations = consultations or {}
        self.sub_steps = [AssignStep(
            step_id='validation-qa', role='qa', output_key='validation_report',
            message_template=(
                'Validate the selected checkout in {cwd}. Request: {task_description}\n'
                'Existing local edits are allowed and may affect check results. Disclose them in the report; '
                'do not claim these checks cover only committed source. '
                'Read repository instructions and command definitions. Run applicable tests, '
                'typecheck and lint once each, avoiding duplicate coverage from combined commands. '
                'Checks are authorized by this workflow unless task or repository rules prohibit them. '
                'Do not edit source, repair, install dependencies, start watch mode, or run browser tests. '
                'Missing tools, dependencies, timeouts or interrupted checks are blocked. '
                'Return ONLY a JSON object with a nonempty checks array. Each entry must have '
                'category (tests, typecheck, lint), command, status (passed, failed, skipped, blocked), '
                'and reason. Include all three categories; skip only when explicitly prohibited or '
                'no repository command exists. Preserve command output evidence in reason. '
                'Use passed only for an actually executed successful command.'
            )), AssignStep(
                step_id='validation-repair', role='implementer', output_key='implementation',
                message_template=(
                    'Make one focused repair for final validation failures, then commit. '
                    'Do not run checks or publish. Preserve task scope; stop with SCOPE_ESCALATION if it must expand. '
                    'For small-change and ui-small keep one clear outcome, straightforward acceptance criteria, '
                    'a localized change using existing patterns and contracts, and a comfortably reviewable diff. '
                    'Above five implementation files across the full task diff, explain why it remains small; '
                    'file count alone must not trigger escalation. Stop for migrations, new background services '
                    'or recovery systems, substantial public contract or architectural changes, or broader '
                    'dependencies or separate design decisions that break those conditions. '
                    'Disclose UI changes and pending UX review. '
                    '\nRequest: {task_description}\n'
                    'UX plan: {ux_assessment}\nArchitecture plan: {plan}\n'
                    'Reviewer findings: {review_text}\n'
                    'Validation results: {validation_report}' + DEFER_CHECKS
                )), *reviews]

    async def execute(self, ctx):
        v = ctx.variables
        root = v['cwd']
        v.pop('validation_commit', None)
        async def stop(kind, reason, actions, checks=None):
            v['validation_stop'] = {
                'kind': kind, 'reason': reason, 'actions': actions,
                'checks': checks or [], 'repairs_used': v.get('validation_repairs_used', 0),
                'repair_limit': MAX_REPAIRS,
            }
            await ctx.persist()
            return StepResult(success=False, error=reason)

        async def identity():
            return await _run(root, 'git', 'rev-parse', 'HEAD')
        try:
            resume_request = v.pop('validation_resume_request', None)
            if resume_request:
                # Only explicit Continue grants another bounded repair cycle.
                v.setdefault('validation_history', []).append({
                    'request_id': resume_request,
                    'attempts': [deepcopy(v.get(f'validation_attempt_{i}')) for i in range(MAX_REPAIRS + 1)],
                    'report': v.get('validation_report'),
                    'stop': deepcopy(v.get('validation_stop')),
                })
                for i in range(MAX_REPAIRS + 1):
                    v.pop(f'validation_attempt_{i}', None)
                v.pop('validation_report', None)
                v.pop('validation_repair_used', None)
                v['validation_repairs_used'] = 0
                v['validation_failure_signatures'] = []
                v.pop('validation_stop', None)
            elif v.get('validation_stop'):
                return StepResult(success=False, error=v['validation_stop']['reason'])
            await ctx.persist()
            for attempt in range(MAX_REPAIRS + 1):
                key = f'validation_attempt_{attempt}'
                previous = v.get(key)
                if previous:
                    return await stop('interrupted', 'Validation was already attempted; explicit Continue is required.',
                                      ['Inspect the saved QA results, then choose Continue to authorize a fresh validation and repair cycle.'])
                head = await identity()
                for review in self.reviews:
                    if v.get(f'{review.output_namespace}_decision') != 'approved' or v.get(f'{review.output_namespace}_commit') != head:
                        # Small-change has two alternative code-review steps sharing one namespace.
                        return await stop('review', 'Final validation requires current source approvals.',
                                          ['Obtain source review approval for the current commit before continuing.'])
                attempt_id = uuid4().hex
                v['validation_attempt_id'] = attempt_id
                v['validation_expected_commit'] = head
                v[key] = {'attempt_id': attempt_id, 'commit': head, 'status': 'running'}
                await ctx.persist()
                # Keep the step identity for durable history, but isolate the QA
                # conversation from earlier passes and their once-only limits.
                qa = self.sub_steps[0]
                qa_pass = AssignStep(
                    step_id=qa.step_id, role=qa.role, output_key=qa.output_key,
                    # Apply the retry contract at execution time so the saved
                    # workflow definition signature remains compatible.
                    message_template=qa.message_template + (
                        '\nThis is a newly authorized validation pass, attempt {validation_attempt_id}, '
                        'for commit {validation_expected_commit}. Run checks afresh in this pass even if '
                        'the same commit was checked earlier: dependencies or the environment may have changed. '
                        'The once-only limit applies to this pass, with one exception: if a command '
                        'cannot run because of a sandbox permission restriction, such as PermissionError '
                        'binding a local test-server socket, use the execution tool approval mechanism '
                        'to request permission for that exact command and retry it once in the same cwd. '
                        'This scoped environment recovery is part of the current QA pass and does not '
                        'consume a source repair round or require the user to run the command manually. '
                        'First stop and reap any still-running process from the restricted invocation. '
                        'Do not change sandbox configuration, grant persistent permissions, install '
                        'dependencies, or request permissions for unrelated commands. Honor repository '
                        'and task restrictions and any approval denial; never bypass them or retry '
                        'a denied request. If the approval mechanism is unavailable, denied, or the '
                        'single retry still cannot run, report blocked with the exact restriction '
                        'and recovery attempt. Do not retry ordinary source/test failures this way. '
                        'Do not reuse earlier check results. '
                        'Include top-level attempt_id equal to {validation_attempt_id} and '
                        'commit equal to {validation_expected_commit} in the JSON report. '
                        'Report repairable source and test errors as failed, including stale test doubles. '
                        'Report permission, dependency, service, credential, and infrastructure issues as blocked. '
                        'If one command has both, return separate checks entries for its failures and blockers; '
                        'an interruption must not hide failures already observed. '
                        'For each failed or blocked entry include action with the concrete recovery step. '
                        'If the scoped retry resolves the environment restriction, report the final '
                        'command outcome and include both invocations in its evidence; preserve any '
                        'independent source/test failures observed in either invocation. Only report '
                        'unresolved environment restrictions as blocked. For remaining blockers identify '
                        'what could not be recovered automatically and the minimum user action needed. '
                        'For failed entries include fingerprint using stable test names and error types, '
                        'without timestamps or temporary paths. Recheck the environment on every pass.'
                    ),
                    thread_key=f'{qa.step_id}:{attempt_id}',
                )
                result = await ctx.execute_step(qa_pass)
                v.update(result.output)
                if not result.success:
                    return await stop('qa', result.error or 'QA did not complete.',
                                      ['Resolve the QA agent error, then choose Continue to run fresh validation.'])
                report = validation_report(v['validation_report'])
                if report.get('attempt_id') != attempt_id or report.get('commit') != head:
                    raise ValueError('QA report does not match the current validation attempt and commit')
                checks = report['checks']
                if (not isinstance(checks, list) or not checks
                        or any(not isinstance(c, dict) for c in checks)
                        or {c.get('category') for c in checks} != {'tests', 'typecheck', 'lint'}):
                    raise ValueError('Validation must report tests, typecheck and lint')
                if any(c['status'] not in {'passed', 'failed', 'skipped', 'blocked'} or
                       not isinstance(c.get('command'), str) or not isinstance(c.get('reason'), str) or
                       (c['status'] in {'passed', 'failed'} and not c['command'].strip()) or
                       (c['status'] in {'skipped', 'blocked'} and not c['reason'].strip()) for c in checks):
                    raise ValueError('Invalid validation evidence')
                # Missing optional guidance must not prevent repairing valid
                # failure evidence. Older agents may omit the new fields.
                for check in checks:
                    if not isinstance(check.get('action'), str) or not check['action'].strip():
                        check['action'] = (
                            f"Resolve the reported environment restriction for {check['command']} in {root}, then choose Continue."
                            if check['status'] == 'blocked' else
                            'Repair the reported source or test failure and commit the change.'
                        )
                    if not isinstance(check.get('fingerprint'), str) or not check['fingerprint'].strip():
                        check['fingerprint'] = check['reason']
                if await identity() != head:
                    raise ValueError('Checkout changed during final validation')
                v[key] = {'attempt_id': attempt_id, 'commit': head, 'checks': checks, 'status': 'finished'}
                await ctx.persist()
                if all(c['status'] in {'passed', 'skipped'} for c in checks):
                    v.pop('validation_stop', None)
                    return StepResult(success=True, output={'validation_commit': head, 'validation_checks': checks,
                        'validation_summary': json.dumps(report)})
                failures = [c for c in checks if c['status'] == 'failed']
                blockers = [c for c in checks if c['status'] == 'blocked']
                if not failures:
                    return await stop('environment', 'Validation is blocked by the execution environment.',
                                      [c.get('action') or c['reason'] for c in blockers] +
                                      ['After resolving these blockers, choose Continue to rerun QA.'], checks)
                signature = sorted(c.get('fingerprint') or c['reason'] for c in failures)
                signatures = v.setdefault('validation_failure_signatures', [])
                signatures.append(signature)
                used = v.get('validation_repairs_used', int(bool(v.get('validation_repair_used'))))
                v['validation_repairs_used'] = used
                repeated = len(signatures) >= 3 and signatures[-3:] == [signature] * 3
                if used >= MAX_REPAIRS or repeated:
                    reason = ('The same validation failures persisted after two repair rounds.' if repeated else
                              f'Validation still fails after {MAX_REPAIRS} repair rounds.')
                    return await stop('repair_limit', reason,
                                      ['Inspect the failing checks and repair commits; fix the underlying issue or clarify the task.',
                                       'Resolve any environment blockers listed below.',
                                       'Choose Continue to authorize another cycle of up to three repairs.'], checks)
                v['validation_repairs_used'] = used + 1
                await ctx.persist()
                repair_step = self.sub_steps[1]
                repair = await ctx.execute_step(AssignStep(
                    step_id=repair_step.step_id, role=repair_step.role, output_key=repair_step.output_key,
                    message_template=repair_step.message_template + (
                        '\nRepair the failed source/test checks, including outdated test doubles. '
                        'Mixed environment blockers must not prevent these repairs. '
                        'Do not bypass tests, weaken assertions, change permissions, or conceal blockers. '
                        'Commit the repair; QA will run checks again after source approval.'
                    ),
                ))
                v.update(repair.output)
                if not repair.success:
                    return await stop('repair', repair.error or 'The implementer could not complete the repair.',
                                      ['Inspect the implementer response, resolve its blocker, then choose Continue.'], checks)
                if 'SCOPE_ESCALATION' in v.get('implementation', ''):
                    return await stop('scope', 'Validation repair requires a scope decision.',
                                      ['Review the implementer’s scope request and clarify or split the task before continuing.'], checks)
                if await identity() == head:
                    return await stop('no_progress', 'The repair produced no new committed changes.',
                                      ['Inspect the implementer response and failing checks; provide a concrete repair before continuing.'], checks)
                for review in self.reviews:
                    while True:
                        v[review.source_key] = v['implementation']
                        v[f'{review.source_key}_files'] = v.get('implementation_files', [])
                        reviewed = await ctx.execute_step(review)
                        v.update(reviewed.output)
                        decision = v.get(f'{review.output_namespace}_decision')
                        if reviewed.success and decision == 'consultation_required':
                            consultation = self.consultations.get(reviewed.next_step)
                            if consultation:
                                advice = await ctx.execute_step(consultation)
                                v.update(advice.output)
                                if advice.success:
                                    continue
                                return await stop('review', advice.error or 'Specialist consultation failed.',
                                                  ['Resolve the consultation blocker before continuing.'], checks)
                        if not reviewed.success or decision not in {'approved', 'changes_requested'}:
                            return await stop('review', reviewed.error or 'Reviewer must return an approval decision for the validation repair.',
                                              ['Resolve the reviewer response before continuing.'], checks)
                        if decision == 'approved':
                            break
                        count = v.get('source_revision_count', 0)
                        if count >= MAX_SOURCE_REVISIONS:
                            failure = source_revision_failure(ctx, [f'{review.output_namespace}_decision'])
                            v.update(failure.output)
                            return await stop('review', failure.error,
                                              ['Inspect the remaining reviewer findings and clarify or split the task.'], checks)
                        v['source_revision_count'] = count + 1
                        await ctx.persist()
                        revised = await ctx.execute_step(AssignStep(
                            step_id='validation-source-revision', role='implementer', output_key='implementation',
                            message_template=repair_step.message_template + (
                                '\nResolve the reviewer findings above against the UX and architecture plans. '
                                'Commit the changes and return directly to the reviewer. Do not run checks or publish.'
                            ),
                        ))
                        v.update(revised.output)
                        if not revised.success or 'SCOPE_ESCALATION' in v.get('implementation', ''):
                            return await stop('review', revised.error or 'Source revision requires a scope decision.',
                                              ['Inspect the implementer report and resolve its blocker before continuing.'], checks)
                await ctx.persist()
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
            return await stop('validation', f'Final validation blocked: {exc}',
                              ['Inspect the QA report and checkout state, resolve the reported problem, then choose Continue.'])
