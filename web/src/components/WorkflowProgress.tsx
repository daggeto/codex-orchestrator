import { Link } from 'react-router';
import { useEffect, useRef } from 'react';
import type { OwnedAgent, WorkflowProgress as Progress } from '../types';
import { ParticipantStrip } from './TaskContext';

const statusLabels: Record<string, string> = {
  pending: 'Not reached', active: 'Working', completed: 'Complete',
  waiting_input: 'Needs your input', failed: 'Failed', interrupted: 'Interrupted',
  cancelled: 'Cancelled', paused: 'Paused',
};

export function WorkflowProgress({ progress, agents, taskId, selected, onSelect }: {
  progress: Progress; agents: OwnedAgent[]; taskId: string; selected?: string; onSelect: (id: string) => void;
}) {
  const list = useRef<HTMLOListElement>(null);
  const current = progress.steps.find(step => step.current);
  useEffect(() => {
    const container = list.current;
    const active = container?.querySelector<HTMLElement>('[aria-current="step"]');
    if (container && active) container.scrollLeft = active.offsetLeft;
  }, [current?.step_id]);
  const participants = (ids: string[]) => agents.filter(agent => ids.includes(agent.agent_id));
  return <section className="workflow-progress" aria-label="Workflow progress">
    <div className="workflow-progress-heading"><strong>Current workflow</strong>{current && <span>{current.label} · {statusLabels[current.status] || current.status}</span>}</div>
    <ol ref={list} className="workflow-stepper">{progress.steps.map((step, index) => <li key={step.step_id}
      className={`workflow-progress-step ${step.current ? 'is-current' : ''} ${step.status === 'completed' ? 'is-complete' : ''}`}
      aria-current={step.current ? 'step' : undefined}>
      <span className="workflow-step-number" aria-hidden="true">{step.status === 'completed' ? '✓' : index + 1}</span>
      <div>{step.agent_id ? <button aria-pressed={selected === step.agent_id} onClick={() => onSelect(step.agent_id!)}>{step.label}</button>
        : <strong>{step.label}</strong>}
        <small>{step.role && <span>{step.role} · </span>}{statusLabels[step.status] || step.status}</small>
      </div>
      {step.agent_id && <Link className="workflow-step-detail" aria-label={`Open ${step.label} agent details`}
        to={`/agents/${step.agent_id}?task_id=${encodeURIComponent(taskId)}`}>↗</Link>}
    </li>)}</ol>
    {!progress.steps.length && <p className="muted">Waiting for workflow steps…</p>}
    {(progress.history.length > 0 || progress.other_agent_ids.length > 0) && <details className="workflow-history">
      <summary>Previous workflow attempts{progress.history.length > 0 ? ` (${progress.history.length})` : ''}</summary>
      {progress.history.map(attempt => <article key={attempt.id}>
        <p><strong>{attempt.status ? statusLabels[attempt.status] || attempt.status : 'Stopped'}</strong> · <time dateTime={attempt.started_at}>{new Date(attempt.started_at).toLocaleString()}</time></p>
        {attempt.stopped_at && <small>Ended <time dateTime={attempt.stopped_at}>{new Date(attempt.stopped_at).toLocaleString()}</time></small>}
        <p>{attempt.reason}</p>
        <ParticipantStrip agents={participants(attempt.agent_ids)} taskId={taskId} />
      </article>)}
      {progress.other_agent_ids.length > 0 && <article><p className="muted">Other recorded agents</p>
        <ParticipantStrip agents={participants(progress.other_agent_ids)} taskId={taskId} />
      </article>}
    </details>}
  </section>;
}
