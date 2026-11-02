"""Read-only workflow progress and historical execution attempts."""


async def presentation(orch, task, workflow, agents, executions):
    if task['kind'] != 'workflow':
        return None
    definition = orch.workflows.get(workflow['workflow_name'] if workflow else task['settings'].get('workflow'))
    state = workflow['state'] if workflow else {}
    team = state.get('team', {})
    run_steps = [row for row in executions if workflow and row['run_id'] == workflow['run_id']]
    latest = {row['step_id']: row for row in run_steps if not row['parent_id']}
    current = state.get('current_step')
    waiting = state.get('phase') == 'waiting_for_plan_approval'
    progress = []
    for step in definition.steps if definition else []:
        execution = latest.get(step.step_id)
        status = execution['status'] if execution else 'pending'
        if step.step_id == current:
            if waiting:
                status = 'waiting_input'
            elif workflow['status'] in {'failed', 'interrupted', 'cancelled', 'paused'}:
                status = workflow['status']
        progress.append({'step_id': step.step_id, 'label': step.name, 'role': getattr(step, 'role', None),
                         'agent_id': team.get(getattr(step, 'role', None)), 'status': status,
                         'current': step.step_id == current and workflow['status'] != 'completed'})

    # A continuation keeps its run ID but starts a new team. Task status events
    # preserve attempt boundaries and stop reasons even after run state changes.
    events = await orch.store.rows("SELECT id,payload_json,created_at FROM task_events WHERE task_id=? AND kind='task/status' ORDER BY id", (task['task_id'],))
    attempts = []
    for event in events:
        payload = event['payload']
        if payload.get('phase') == 'Starting agents':
            attempts.append({'id': str(event['id']), 'started_at': event['created_at'],
                             'stopped_at': None, 'status': None, 'reason': None})
        elif attempts and payload.get('status') in {'failed', 'interrupted', 'cancelled', 'completed', 'waiting_input'}:
            attempts[-1].update(stopped_at=event['created_at'], status=payload['status'], reason=payload.get('phase'))
    history = []
    for index, attempt in enumerate(attempts[:-1]):
        end = attempts[index + 1]['started_at']
        evidence = [row for row in executions if attempt['started_at'] <= row['created_at'] < end]
        ids = {row['agent_id'] for row in evidence if row['agent_id']}
        ids.update(agent['agent_id'] for agent in agents
                   if agent['role'] in team and attempt['started_at'] <= agent['created_at'] < end)
        history.append({**attempt, 'agent_ids': sorted(ids),
                        'reason': attempt['reason'] or 'No stop reason was recorded.'})
    current_ids = set(team.values())
    recorded_ids = {aid for attempt in history for aid in attempt['agent_ids']}
    other = [agent['agent_id'] for agent in agents if agent['agent_id'] not in current_ids | recorded_ids]
    return {'steps': progress, 'agent_ids': sorted(current_ids), 'history': list(reversed(history)),
            'other_agent_ids': other}
