"""Final, commit-bound validation with one explicitly bounded repair."""
from __future__ import annotations

import json
from copy import deepcopy

from orchestrator.workflows.steps import AssignStep, Step, StepResult
from orchestrator.workflows.merge_request import _run

DEFER_CHECKS = (
    "\n\nWorkflow validation policy: source stages must not run tests, typecheck, or lint. "
    "QA owns final automated checks after source approvals. Follow repository restrictions. "
    "Report pending browser acceptance separately."
)


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
    def __init__(self, reviews, **kwargs):
        super().__init__(**kwargs)
        self.reviews = reviews
        self.sub_steps = [AssignStep(
            step_id='validation-qa', role='qa', output_key='validation_report',
            message_template=(
                'Validate the final committed changes in {cwd}. Request: {task_description}\n'
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
                    'For small-change and ui-small preserve the five implementation file limit, no migrations, '
                    'new services or substantial public contract changes. Disclose UI changes and pending UX review. '
                    '\nRequest: {task_description}\n'
                    'Validation results: {validation_report}' + DEFER_CHECKS
                )), *reviews]

    async def execute(self, ctx):
        v = ctx.variables
        root = v['cwd']
        v.pop('validation_commit', None)
        async def identity():
            if await _run(root, 'git', 'status', '--porcelain', '--untracked-files=all'):
                raise ValueError('Final validation requires a clean committed checkout')
            return await _run(root, 'git', 'rev-parse', 'HEAD')
        try:
            resume_request = v.pop('validation_resume_request', None)
            if resume_request and any(v.get(f'validation_attempt_{i}') for i in range(2)):
                # Preserve evidence and the repair budget across explicit retries.
                v.setdefault('validation_history', []).append({
                    'request_id': resume_request,
                    'attempts': [deepcopy(v.get(f'validation_attempt_{i}')) for i in range(2)],
                    'report': v.get('validation_report'),
                })
                for i in range(2):
                    v.pop(f'validation_attempt_{i}', None)
                v.pop('validation_report', None)
            await ctx.persist()
            for attempt in range(2):
                key = f'validation_attempt_{attempt}'
                previous = v.get(key)
                if previous:
                    return StepResult(success=False, error='Validation was already attempted; inspect persisted results before explicit follow-up')
                head = await identity()
                for review in self.reviews:
                    if v.get(f'{review.output_namespace}_decision') != 'approved' or v.get(f'{review.output_namespace}_commit') != head:
                        # Small-change has two alternative code-review steps sharing one namespace.
                        return StepResult(success=False, error='Final validation requires current source approvals')
                v[key] = {'commit': head, 'status': 'running'}
                await ctx.persist()
                result = await ctx.execute_step(self.sub_steps[0])
                v.update(result.output)
                if not result.success:
                    return result
                report = validation_report(v['validation_report'])
                checks = report['checks']
                if not isinstance(checks, list) or not checks or {c['category'] for c in checks} != {'tests', 'typecheck', 'lint'}:
                    raise ValueError('Validation must report tests, typecheck and lint')
                if any(c['status'] not in {'passed', 'failed', 'skipped', 'blocked'} or
                       not isinstance(c.get('command'), str) or not isinstance(c.get('reason'), str) or
                       (c['status'] in {'passed', 'failed'} and not c['command'].strip()) or
                       (c['status'] in {'skipped', 'blocked'} and not c['reason'].strip()) for c in checks):
                    raise ValueError('Invalid validation evidence')
                if await identity() != head:
                    raise ValueError('Checkout changed during final validation')
                v[key] = {'commit': head, 'checks': checks, 'status': 'finished'}
                await ctx.persist()
                if all(c['status'] in {'passed', 'skipped'} for c in checks):
                    return StepResult(success=True, output={'validation_commit': head, 'validation_checks': checks,
                        'validation_summary': json.dumps(report)})
                if attempt or v.get('validation_repair_used') or any(c['status'] == 'blocked' for c in checks):
                    return StepResult(success=False, error='Final validation failed or blocked; inspect validation results')
                v['validation_repair_used'] = True
                await ctx.persist()
                repair = await ctx.execute_step(self.sub_steps[1])
                v.update(repair.output)
                if not repair.success:
                    return repair
                if 'SCOPE_ESCALATION' in v.get('implementation', ''):
                    return StepResult(success=False, error='Validation repair requires a scope decision')
                for review in self.reviews:
                    v[review.source_key] = v['implementation']
                    v[f'{review.source_key}_files'] = v.get('implementation_files', [])
                    reviewed = await ctx.execute_step(review)
                    v.update(reviewed.output)
                    if not reviewed.success or v.get(f'{review.output_namespace}_decision') != 'approved':
                        return StepResult(success=False, error='Validation repair did not pass source review')
                await ctx.persist()
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
            return StepResult(success=False, error=f'Final validation blocked: {exc}')
