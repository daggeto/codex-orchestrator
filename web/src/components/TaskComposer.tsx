import { useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { post } from '../api';
import { Chat } from './Chat';
import { useResource } from '../useResource';
import type { Role, WorkflowDefinition, WorkspaceRecord } from '../types';

export function TaskComposer({ workspace }: { workspace: WorkspaceRecord }) {
  const navigate = useNavigate();
  const [kind, setKind] = useState('workflow');
  const [workflow, setWorkflow] = useState('feature');
  const [role, setRole] = useState('');
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('');
  const [recipe, setRecipe] = useState('');
  const [isolation, setIsolation] = useState('auto');
  const [publish, setPublish] = useState(false);
  const key = useRef(crypto.randomUUID());
  const fingerprint = useRef('');
  const workflows = useResource<{ workflows: WorkflowDefinition[] }>('/api/workflows', 0);
  const roles = useResource<{ roles: Role[] }>('/api/roles', 0);
  const recipes = useResource<{ skills: { name: string; roles: string[] }[] }>('/api/skills', 0);
  const selectedWorkflow = workflows.data?.workflows.find(item => item.name === workflow);
  return <><p>New task in {workspace.kind === 'main' ? 'Main' : workspace.branch}: <code>{workspace.path}</code></p>
    <Chat label="Start a task" draftKey={`workspace:${workspace.workspace_id}`} messages={[]} submitLabel="Start task" disabled={workspace.status !== 'active'}
      toolbar={<div className="composer-settings">
        <label>Execution<select value={kind} onChange={event => setKind(event.target.value)}><option value="workflow">Workflow</option><option value="agent">Agent conversation</option></select></label>
        {kind === 'workflow' ? <><label>Workflow<select value={workflow} onChange={event => setWorkflow(event.target.value)}>
          {workflows.data?.workflows.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
          {selectedWorkflow?.publication_policy === 'optional' && <label><input type="checkbox" checked={publish} onChange={event => setPublish(event.target.checked)} />Create draft PR/MR</label>}
          {selectedWorkflow?.publication_policy === 'required' && <p>This workflow publishes the approved commits.</p>}</> : <>
          <label>Role<select value={role} onChange={event => { setRole(event.target.value); setRecipe(''); }}><option value="">Default Codex</option>{roles.data?.roles.map(item => <option key={item.name}>{item.name}</option>)}</select></label>
          <label>Model<input value={model} onChange={event => setModel(event.target.value)} placeholder="Role default" /></label>
          <label>Effort<select value={effort} onChange={event => setEffort(event.target.value)}><option value="">Role default</option>{['low','medium','high','xhigh'].map(value => <option key={value}>{value}</option>)}</select></label>
          <label>Recipe<select value={recipe} onChange={event => setRecipe(event.target.value)}><option value="">None</option>{recipes.data?.skills.filter(item => !item.roles?.length || item.roles.includes(role)).map(item => <option key={item.name}>{item.name}</option>)}</select></label>
        </>}
        {workspace.kind === 'main' && <label>Workspace<select value={isolation} onChange={event => setIsolation(event.target.value)}><option value="auto">Isolate when needed</option><option value="always">New worktree</option><option value="never">Use Main</option></select></label>}
      </div>}
      onSubmit={async prompt => {
        const settings = kind === 'workflow' ? { workflow, variables: selectedWorkflow?.publication_policy === 'required' ? {} : { create_mr: publish } } : { role: role || undefined, model: model || undefined, effort: effort || undefined, recipe: recipe || undefined };
        const payload = { workspace_id: workspace.workspace_id, prompt, kind, isolation, settings };
        const next = JSON.stringify(payload);
        if (fingerprint.current && fingerprint.current !== next) key.current = crypto.randomUUID();
        fingerprint.current = next;
        const result = await post<{ task_id: string }>('/api/tasks', { ...payload, idempotency_key: key.current });
        key.current = crypto.randomUUID();
        navigate(`/tasks/${encodeURIComponent(result.task_id)}`);
      }} />
    {(workflows.error || roles.error || recipes.error) && <p className="error">Task settings could not be loaded. {workflows.error || roles.error || recipes.error}</p>}
  </>;
}
