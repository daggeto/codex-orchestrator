import { useNavigation } from '../navigation';
import { Link, useParams, useSearchParams } from 'react-router';
import { AgentHistory } from '../components/AgentConversation';
import { ParticipantStrip, TaskContext } from '../components/TaskContext';
import { ResourceState } from '../components/ui/ResourceState';
import { useResource } from '../useResource';
import type { OwnedAgent } from '../types';
export function AgentPage() {
  const { agentId } = useParams();
  const [params] = useSearchParams();
  const taskId = params.get('task_id');
  const agent = useResource<{ agent: OwnedAgent }>(`/api/agents/${agentId}/events?limit=1`);
  const context = useNavigation().selected;
  if (taskId && !context.data) return <ResourceState error={!!context.error}>{context.missing ? 'Task context is unavailable.' : context.error || 'Loading task context…'}</ResourceState>;
  const participant = context.data?.agents.find(a => a.agent_id === agentId);
  if (taskId && !participant) return <ResourceState error>This agent does not participate in the selected task. <Link to={`/tasks/${encodeURIComponent(taskId)}`}>Open task</Link></ResourceState>;
  if (!agent.data) return <ResourceState error={!!agent.error}>{agent.error || 'Loading agent…'}</ResourceState>;
  const info = participant || agent.data.agent;
  return <section className="task-page agent-page">{context.data ? <><TaskContext task={context.data.task} title={info.role} />
    <ParticipantStrip agents={context.data.agents} taskId={taskId!} selected={agentId} />
    <div className={context.data.pending_input.length ? 'pending-input' : 'agent-context-link'}><Link to={`/tasks/${encodeURIComponent(taskId!)}`}>{context.data.pending_input.length ? 'Your input is needed. Open task to respond →' : 'Open task and conversation controls →'}</Link></div></> : <><h1>{info.role}</h1><p className="muted">History across all tasks. Open this agent from a task to view that conversation in context.</p>{info.task_id && <Link to={`/tasks/${encodeURIComponent(info.task_id)}`}>Open original task</Link>}</>}
    {(agent.error || context.error) && <p role="alert" className="error">{agent.error || context.error} · Showing saved context.</p>}
    <section className="conversation-panel" aria-label="Agent conversation"><AgentHistory key={`${agentId}:${taskId || ''}`} agentId={agentId!} taskId={taskId || undefined} label={info.role} /></section>
    <details className="execution-details"><summary>Agent details</summary><p>Agent ID: <code>{agentId}</code></p><p>{info.backend} · {info.status}{info.retired_at && ' · Retired'}</p></details>
  </section>;
}
