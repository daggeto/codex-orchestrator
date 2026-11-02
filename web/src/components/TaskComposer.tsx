import { Checkbox } from './ui/primitives/checkbox';
import { NativeSelect } from './ui/primitives/native-select';
import { Input } from './ui/primitives/input';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { get, post, submissionIdentity, clearSubmissionIdentity } from '../api';
import { Card } from './ui/Card';
import { Badge } from './ui/Badge';
import { Chat } from './Chat';
import { Button } from './ui/Button';
import { useOperation } from '../useOperation';
import type { OperationAttempt } from '../operationState';
import { useResource } from '../useResource';
import type { Role, WorkflowDefinition, WorkspaceRecord } from '../types';

export function TaskComposer({ workspace, stale = false }: { workspace: WorkspaceRecord; stale?: boolean }) {
  const navigate = useNavigate();
  const [kind, setKind] = useState('workflow');
  const [workflow, setWorkflow] = useState('small-change');
  const [role, setRole] = useState('');
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('');
  const [recipe, setRecipe] = useState('');
  const [isolation, setIsolation] = useState('auto');
  const [publish, setPublish] = useState(false);
  const workflows = useResource<{ workflows: WorkflowDefinition[] }>('/api/workflows', 0);
  const roles = useResource<{ roles: Role[] }>('/api/roles', 0);
  const recipes = useResource<{ skills: { name: string; roles: string[] }[] }>('/api/skills', 0);
  const operation = useOperation<Record<string, unknown>>(`workspace:${workspace.workspace_id}:task`, 'task');
  const [recoveryError, setRecoveryError] = useState('');
  const [recovering, setRecovering] = useState(false);
  useEffect(() => {
    if (operation.current) return;
    try {
      const legacy = JSON.parse(sessionStorage.getItem(`workspace:${workspace.workspace_id}:submission`) || 'null');
      if (typeof legacy?.key === 'string' && typeof legacy.fingerprint === 'string') operation.prepare(JSON.parse(legacy.fingerprint), 0, legacy.key);
    } catch { /* Invalid legacy evidence is retained for inspection. */ }
  }, [workspace.workspace_id, operation.current]);
  useEffect(() => {
    const attempt = operation.current;
    if (!attempt || !operation.unresolved) return;
    const controller = new AbortController();
    // Bounded history observation is evidence of acceptance only when the key matches.
    void get<{ tasks: { task_id: string; idempotency_key?: string }[] }>(`/api/workspaces/${encodeURIComponent(workspace.workspace_id)}/tasks?limit=100`, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const task = result.tasks.find(item => item.idempotency_key === attempt.id);
      if (task) { operation.settle(attempt, 'accepted', { task_id: task.task_id }); clearSubmissionIdentity(`workspace:${workspace.workspace_id}:submission`, attempt.id); }
    }).catch(() => { /* Absence or failed observation never proves rejection. */ });
    return () => controller.abort();
  }, [operation.current?.id, operation.unresolved, workspace.workspace_id]);
  const selectedWorkflow = workflows.data?.workflows.find(item => item.name === workflow);
  const ready = kind === 'workflow' ? !!selectedWorkflow && !workflows.error :
    (!role || !!roles.data?.roles.some(item => item.name === role) && !roles.error) &&
    (!recipe || !!recipes.data?.skills.some(item => item.name === recipe) && !recipes.error);
  async function send(attempt: OperationAttempt<Record<string, unknown>>) {
    try {
      const result = await post<{ task_id: string }>('/api/tasks', { ...attempt.request, idempotency_key: attempt.id });
      operation.settle(attempt, 'accepted', { task_id: result.task_id });
      clearSubmissionIdentity(`workspace:${workspace.workspace_id}:submission`, attempt.id);
      navigate(`/tasks/${encodeURIComponent(result.task_id)}`);
    } catch (reason) {
      const receipt = operation.settle(attempt, 'uncertain', undefined, String(reason));
      if (receipt.status === 'accepted') return;
      throw reason;
    }
  }
  return <Card className="task-composer"><div className="composer-heading"><div><h2>Start a task</h2><p className="resource-path">{workspace.path}</p></div><Badge>{workspace.kind === 'main' ? 'Main' : workspace.branch || 'Worktree'}</Badge></div>
    <div className="execution-summary"><span>Checkout: {workspace.kind !== 'main' ? 'Reuse selected worktree' : isolation === 'never' ? 'Use Main' : isolation === 'always' ? 'New worktree' : 'Isolate when needed'}</span><span>{kind === 'agent' ? 'Agent conversation' : selectedWorkflow?.publication_policy === 'required' ? 'Publishes approved commits' : selectedWorkflow?.publication_policy === 'optional' && publish ? 'Create draft PR/MR' : 'PR/MR publication disabled'}</span></div>
    <Chat label="Start a task" draftKey={`workspace:${workspace.workspace_id}`} messages={[]} attachmentContext={{ workspaceId: workspace.workspace_id }} submitLabel="Start task" disabled={stale || workspace.status !== 'active'} submitDisabled={!ready || !!operation.unresolved || recovering} submitReason={stale ? 'Workspace state is stale. Wait for fresh data before starting; your draft stays editable.' : !ready ? 'Load valid settings for this execution mode before starting. Your draft stays editable.' : operation.unresolved ? 'A saved request needs reconciliation before another task can start.' : undefined}
      toolbar={<div className="composer-settings">
        <label>Execution<NativeSelect value={kind} onChange={event => setKind(event.target.value)}><option value="workflow">Workflow</option><option value="agent">Agent conversation</option></NativeSelect></label>
        {kind === 'workflow' ? <><label>Workflow<NativeSelect value={workflow} onChange={event => setWorkflow(event.target.value)}>
          {workflows.data?.workflows.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</NativeSelect></label>
          <p>{selectedWorkflow?.description}</p>
          <p>Team: {selectedWorkflow?.required_roles.join(', ')}</p>
          {selectedWorkflow?.publication_policy === 'optional' && <label><Checkbox checked={publish} onCheckedChange={setPublish} />Create draft PR/MR</label>}
          {selectedWorkflow?.publication_policy === 'required' && <p>This workflow publishes the approved commits.</p>}</> : <>
          <label>Role<NativeSelect value={role} onChange={event => { setRole(event.target.value); setRecipe(''); }}><option value="">Default Codex</option>{roles.data?.roles.map(item => <option key={item.name}>{item.name}</option>)}</NativeSelect></label>
          <label>Model<Input value={model} onChange={event => setModel(event.target.value)} placeholder="Role default" /></label>
          <label>Effort<NativeSelect value={effort} onChange={event => setEffort(event.target.value)}><option value="">Role default</option>{['low','medium','high','xhigh'].map(value => <option key={value}>{value}</option>)}</NativeSelect></label>
          <label>Recipe<NativeSelect value={recipe} onChange={event => setRecipe(event.target.value)}><option value="">None</option>{recipes.data?.skills.filter(item => !item.roles?.length || item.roles.includes(role)).map(item => <option key={item.name}>{item.name}</option>)}</NativeSelect></label>
        </>}
        {workspace.kind === 'main' && <label>Workspace<NativeSelect value={isolation} onChange={event => setIsolation(event.target.value)}><option value="auto">Isolate when needed</option><option value="always">New worktree</option><option value="never">Use Main</option></NativeSelect></label>}
      </div>}
      onSubmit={async (prompt, attachments) => {
        const settings = kind === 'workflow' ? { workflow, variables: {
          create_mr: selectedWorkflow?.publication_policy === 'required' ||
            (selectedWorkflow?.publication_policy === 'optional' && publish),
        } } : { role: role || undefined, model: model || undefined, effort: effort || undefined, recipe: recipe || undefined };
        const payload = { workspace_id: workspace.workspace_id, prompt, kind, isolation, settings, ...attachments };
        const key = submissionIdentity(`workspace:${workspace.workspace_id}:submission`, payload);
        await send(operation.prepare(payload, 0, key));
      }} />
    {operation.unresolved && <Button disabled={recovering || stale} onClick={async () => {
      if (!operation.current?.request || recovering) return;
      setRecovering(true); setRecoveryError('');
      try { await send(operation.current); } catch (reason) { setRecoveryError(String(reason)); } finally { setRecovering(false); }
    }}>{recovering ? 'Checking saved attempt…' : 'Check saved task attempt'}</Button>}
    {operation.current?.status === 'accepted' && operation.current.result?.task_id && <p role="status">Task accepted: <Button onClick={() => navigate(`/tasks/${encodeURIComponent(operation.current!.result!.task_id)}`)}>Open task</Button></p>}
    {!operation.durable && <p role="alert">Request storage is unavailable. Recovery works only while this page stays open.</p>}
    {recoveryError && <p role="alert" className="error">{recoveryError}</p>}
    {([['Workflow definitions', workflows], ['Roles', roles], ['Recipes', recipes]] as const).map(([label, resource]) =>
      <div key={label}>{resource.loading && <p role="status">Loading {label.toLowerCase()}…</p>}{resource.error && <p role="alert" className="error">{label}: {resource.error} <Button onClick={resource.refresh}>Retry {label.toLowerCase()}</Button></p>}</div>)}
  </Card>;
}
