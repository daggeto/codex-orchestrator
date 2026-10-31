import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { post } from '../api';
import { useResource } from '../useResource';
import type { InputRequest, TaskView } from '../types';
import { WorkflowTimeline } from '../components/WorkflowTimeline';
import { AgentHistory } from '../components/AgentConversation';
import { Chat } from '../components/Chat';
import { MessageContent } from '../components/MessageContent';

function PendingInput({ input }: { input: InputRequest }) {
  const [response, setResponse] = useState('');
  const [error, setError] = useState('');
  const [resolved, setResolved] = useState(false);
  return <form className="pending-input" onSubmit={async event => {
    event.preventDefault(); setError('');
    try { await post(`/api/input-requests/${input.request_id}/resolve`, { response: input.kind === 'backend_input' && response.trim().startsWith('{') ? JSON.parse(response) : response }); setResolved(true); }
    catch (reason) { setError(String(reason)); }
  }}><p>{input.prompt}</p>{input.kind === 'approval' ? <select required value={response} onChange={event => setResponse(event.target.value)}><option value="">Choose…</option><option value="approved">Approve</option><option value="rejected">Reject</option></select> : <textarea aria-label="Your response" required value={response} onChange={event => setResponse(event.target.value)} />}
    <button disabled={resolved || !response.trim()}>{resolved ? 'Answered' : 'Answer'}</button>{error && <p role="alert" className="error">{error}</p>}
  </form>;
}
export function TaskPage() {
  const { taskId } = useParams();
  const { data, error } = useResource<TaskView>(`/api/tasks/${encodeURIComponent(taskId || '')}`);
  const [failure, setFailure] = useState('');
  if (!data) return <p>{error || 'Loading task…'}</p>;
  const { task, agents, workflow, steps, pending_input } = data;
  const closed = ['completed','cancelled','failed'].includes(task.status);
  const busy = ['queued','waiting_input'].includes(task.status) || task.phase === 'Agent running' || task.phase === 'Starting agents';
  async function action(name: string) { try { await post(`/api/tasks/${encodeURIComponent(taskId || '')}/${name}`, {}); setFailure(''); } catch (reason) { setFailure(String(reason)); } }
  return <><Link to={`/workspaces/${task.workspace_id}`}>Workspace</Link><h1>{task.title}</h1><p className="task-phase" role="status">{task.status} · {task.phase}</p>
    {error && <p className="error">{error}</p>}
    <details><summary>Original request</summary><MessageContent text={task.prompt} /></details>
    {pending_input.map(input => <PendingInput key={input.request_id} input={input} />)}
    <h2>Agents</h2><ul>{agents.map(agent => <li key={agent.agent_id}><Link to={`/agents/${agent.agent_id}`}>{agent.role}</Link> · {agent.status} · {agent.activity?.state}</li>)}</ul>
    {workflow && <><h2>{workflow.workflow_name}</h2><WorkflowTimeline steps={steps} /></>}
    {task.kind === 'agent' && agents[0] && <AgentHistory key={agents[0].agent_id} agentId={agents[0].agent_id} />}
    {task.kind === 'agent' && task.status === 'cancelled' && <button onClick={() => void action('complete')}>Mark complete</button>}
    {task.kind === 'agent' && !closed && <><Chat label="Continue conversation" draftKey={`task:${task.task_id}`} messages={[]} disabled={busy} onSubmit={async message => { await post(`/api/tasks/${encodeURIComponent(task.task_id)}/messages`, { message }); }} />
      <button disabled={busy} onClick={() => void action('complete')}>Mark complete</button></>}
    {!closed && <button onClick={() => void action('cancel')}>Cancel execution</button>}
    {failure && <p role="alert" className="error">{failure}</p>}
  </>;
}
