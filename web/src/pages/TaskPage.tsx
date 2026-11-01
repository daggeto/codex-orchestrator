import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { post, submissionIdentity, clearSubmissionIdentity } from '../api';
import { useNavigation } from '../navigation';
import { useResource } from '../useResource';
import type { InputRequest, TaskView } from '../types';
import { ContinueRun, RestartRun } from '../components/WorkflowRecovery';
import { WorkflowTimeline } from '../components/WorkflowTimeline';
import { AgentHistory } from '../components/AgentConversation';
import { Chat } from '../components/Chat';
import { AttachmentList } from '../components/AttachmentList';
import { PendingInput } from '../components/PendingInput';
import { ParticipantStrip, TaskContext } from '../components/TaskContext';
import { Button } from '../components/ui/Button';
import { ResourceState } from '../components/ui/ResourceState';
import { MessageContent } from '../components/MessageContent';
export function TaskPage() {
  const { taskId } = useParams();
  return <TaskContent key={taskId} taskId={taskId || ''} />;
}
function TaskContent({ taskId }: { taskId: string }) {
  const { data, error, refresh } = useResource<TaskView>(`/api/tasks/${encodeURIComponent(taskId)}`);
  const navigation = useNavigation();
  const navigate = useNavigate();
  const [failure, setFailure] = useState('');
  const [pending, setPending] = useState('');
  const lock = useRef(false);
  const [selected, setSelected] = useState('');
  const [retained, setRetained] = useState<InputRequest[]>([]);
  const [dismissed, setDismissed] = useState<string[]>([]);
  useEffect(() => {
    if (data) setRetained(previous => [...previous, ...data.pending_input.filter(input => !previous.some(old => old.request_id === input.request_id))]);
  }, [data]);

  if (!data) return <ResourceState error={!!error}>{error || 'Loading task…'}</ResourceState>;
  const { task, agents, workflow, steps, pending_input } = data;
  const closed = ['completed','cancelled','failed'].includes(task.status);
  const busy = ['queued','waiting_input'].includes(task.status) || task.phase === 'Agent running' || task.phase === 'Starting agents';
  const participant = agents.find(a => a.agent_id === selected) || agents[0];
  async function action(name: string, body = {}) {
    if (lock.current) return;
    lock.current = true; setPending(name); setFailure('');
    try { await post(`/api/tasks/${encodeURIComponent(taskId)}/${name}`, body); refresh(); navigation.refresh(); }
    catch (reason) { setFailure(String(reason)); }
    finally { lock.current = false; setPending(''); }
  }
  return <section className="task-page"><TaskContext task={task} />
    {error && <p className="error" role="alert">{error} · Showing saved task state.</p>}
    {workflow && ['interrupted', 'failed', 'cancelled', 'paused'].includes(workflow.status) && <section aria-label="Workflow recovery">
      <ContinueRun key={`continue:${workflow.run_id}:${workflow.updated_at}`} run={workflow} disabled={!!error || !!pending} />
      <RestartRun key={`restart:${workflow.run_id}`} run={workflow} disabled={!!error || !!pending}
        select={(_runId, newTaskId) => navigate(newTaskId ? `/tasks/${encodeURIComponent(newTaskId)}` : '/monitor')} />
    </section>}
    {!!data.attachments?.length && <section aria-label="Task attachments"><h2>Task files</h2><p>Available to every task role. Removing this worktree permanently deletes these files.</p><AttachmentList taskId={taskId} attachments={data.attachments} />
      <details><summary>Files by message</summary>{data.messages?.filter(message => message.attachment_ids.length).map(message => <article key={message.message_id}><strong>{message.origin === 'initial' ? 'Original request' : message.origin === 'input_response' ? 'Question response' : 'Follow-up'}</strong><MessageContent text={message.text} /><AttachmentList taskId={taskId} attachments={data.attachments.filter(file => message.attachment_ids.includes(file.attachment_id))} /></article>)}</details></section>}
    <ParticipantStrip agents={agents} taskId={task.task_id} selected={participant?.agent_id} onSelect={setSelected} />
    {[...retained, ...pending_input.filter(input => !retained.some(old => old.request_id === input.request_id))].filter(input => !dismissed.includes(input.request_id)).map(input => <PendingInput key={input.request_id} input={input} attachmentContext={{ workspaceId: task.workspace_id, taskId }} inactive={!pending_input.some(active => active.request_id === input.request_id)} onDismiss={() => setDismissed(previous => [...previous, input.request_id])} onResolved={() => { setDismissed(previous => [...previous, input.request_id]); refresh(); }} />)}
    <section className="conversation-panel" aria-label="Task conversation">{participant ? <AgentHistory key={`${task.task_id}:${participant.agent_id}`} agentId={participant.agent_id} taskId={task.task_id} attachments={data.attachments} label={participant.role} /> : <ResourceState>{closed ? 'No recorded agent conversation.' : 'Waiting for an agent to join…'}</ResourceState>}
      {task.kind === 'agent' && !closed && <Chat label="Continue conversation" rows={3} draftKey={`task:${task.task_id}`} messages={[]} attachmentContext={{ workspaceId: task.workspace_id, taskId }} disabled={busy || !!pending} onSubmit={async (message, attachments) => { const payload = { message, ...attachments }; await post(`/api/tasks/${encodeURIComponent(task.task_id)}/messages`, { ...payload, idempotency_key: submissionIdentity(`task:${taskId}:submission`, payload) }); clearSubmissionIdentity(`task:${taskId}:submission`); refresh(); }} />}
    </section>
    <div className="task-controls">
      {task.kind === 'agent' && (!closed || task.status === 'cancelled') && <Button disabled={busy || !!pending} onClick={() => void action('complete')}>{pending === 'complete' ? 'Completing…' : 'Mark complete'}</Button>}
      {!closed && <Button variant="danger" disabled={!!pending} onClick={() => void action('cancel')}>{pending === 'cancel' ? 'Cancelling…' : 'Cancel execution'}</Button>}
      <Button disabled={!closed || !!pending} onClick={() => void action('archive', { archived: !task.archived_at })}>{pending === 'archive' ? 'Saving…' : task.archived_at ? 'Restore task' : 'Archive task'}</Button>
      {!closed && <small>Complete or cancel this task before archiving.</small>}
    </div>
    {failure && <p role="alert" className="error">{failure}</p>}
    <details className="execution-details"><summary>Execution details and original request</summary><h2>Original request</h2><MessageContent text={task.prompt} /><p className="muted">Task ID: <code>{task.task_id}</code> · {task.kind}</p>{workflow && <><h2>{workflow.workflow_name}</h2>{!steps.length && <ResourceState>Waiting for workflow steps…</ResourceState>}<WorkflowTimeline steps={steps} taskId={task.task_id} /></>}</details>
  </section>;
}
