import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { post } from '../api';
import { useResource } from '../useResource';
import type { InputRequest, TaskView } from '../types';
import { WorkflowTimeline } from '../components/WorkflowTimeline';
import { AgentHistory } from '../components/AgentConversation';
import { Chat } from '../components/Chat';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { PageHeader } from '../components/ui/PageHeader';
import { ResourceState } from '../components/ui/ResourceState';
import { MessageContent } from '../components/MessageContent';

function PendingInput({ input }: { input: InputRequest }) {
  const [response, setResponse] = useState('');
  const [error, setError] = useState('');
  const [resolved, setResolved] = useState(false);
  return <form className="pending-input" onSubmit={async event => {
    event.preventDefault(); setError('');
    try { await post(`/api/input-requests/${input.request_id}/resolve`, { response: input.kind === 'backend_input' && response.trim().startsWith('{') ? JSON.parse(response) : response }); setResolved(true); }
    catch (reason) { setError(String(reason)); }
  }}><h2>{input.kind === 'approval' ? 'Approval needed' : 'Your input is needed'}</h2><p>{input.prompt}</p>{input.kind === 'approval' ? <select aria-label="Approval decision" required value={response} onChange={event => setResponse(event.target.value)}><option value="">Choose…</option><option value="approved">Approve</option><option value="rejected">Reject</option></select> : <textarea aria-label="Your response" required value={response} onChange={event => setResponse(event.target.value)} />}
    <Button type="submit" variant="primary" disabled={resolved || !response.trim()}>{resolved ? 'Answered' : 'Answer'}</Button>{error && <p role="alert" className="error">{error}</p>}
  </form>;
}
export function TaskPage() {
  const { taskId } = useParams();
  const { data, error } = useResource<TaskView>(`/api/tasks/${encodeURIComponent(taskId || '')}`);
  const [failure, setFailure] = useState('');
  if (!data) return <ResourceState error={!!error}>{error || 'Loading task…'}</ResourceState>;
  const { task, agents, workflow, steps, pending_input } = data;
  const closed = ['completed','cancelled','failed'].includes(task.status);
  const busy = ['queued','waiting_input'].includes(task.status) || task.phase === 'Agent running' || task.phase === 'Starting agents';
  async function action(name: string) { try { await post(`/api/tasks/${encodeURIComponent(taskId || '')}/${name}`, {}); setFailure(''); } catch (reason) { setFailure(String(reason)); } }
  return <><PageHeader eyebrow={<Link to={`/workspaces/${task.workspace_id}`}>Workspace</Link>} title={task.title} description={task.kind === 'agent' ? 'Agent conversation' : 'Workflow execution'} /><p className="task-phase" role="status"><Badge>{task.status}</Badge><span>{task.phase}</span></p>
    {error && <p className="error">{error}</p>}
    <Card><details><summary>Original request</summary><MessageContent text={task.prompt} /></details></Card>
    {pending_input.map(input => <PendingInput key={input.request_id} input={input} />)}
    <Card><h2>Agents</h2>{!agents.length && <ResourceState>No agents assigned yet.</ResourceState>}<ul className="resource-list">{agents.map(agent => <li key={agent.agent_id}><Link to={`/agents/${agent.agent_id}`}>{agent.role}</Link><span><Badge>{agent.status}</Badge> {agent.activity?.state}</span></li>)}</ul></Card>
    {workflow && <Card><h2>{workflow.workflow_name}</h2>{!steps.length && <ResourceState>Waiting for workflow steps…</ResourceState>}<WorkflowTimeline steps={steps} /></Card>}
    {task.kind === 'agent' && agents[0] && <Card><h2>Conversation</h2><AgentHistory key={`${task.task_id}:${agents[0].agent_id}`} agentId={agents[0].agent_id} taskId={task.task_id} /></Card>}
    {task.kind === 'agent' && task.status === 'cancelled' && <Button onClick={() => void action('complete')}>Mark complete</Button>}
    {task.kind === 'agent' && !closed && <Card><Chat label="Continue conversation" draftKey={`task:${task.task_id}`} messages={[]} disabled={busy} onSubmit={async message => { await post(`/api/tasks/${encodeURIComponent(task.task_id)}/messages`, { message }); }} />
      <Button disabled={busy} onClick={() => void action('complete')}>Mark complete</Button></Card>}
    {!closed && <div className="task-controls"><Button variant="danger" onClick={() => void action('cancel')}>Cancel execution</Button></div>}
    {failure && <p role="alert" className="error">{failure}</p>}
  </>;
}
