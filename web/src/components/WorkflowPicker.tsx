import { useId } from 'react';
import type { Role, WorkflowDefinition } from '../types';

interface Props { workflows: WorkflowDefinition[]; roles: Role[]; value: string; onChange: (value: string) => void; publish: boolean; onPublish: (value: boolean) => void; disabled: boolean }
export function WorkflowPicker({ workflows, roles, value, onChange, publish, onPublish, disabled }: Props) {
  const id = useId();
  const workflow = workflows.find(item => item.name === value);
  return <section className="workflow-picker">
    <label htmlFor={id}>Workflow</label>
    <select id={id} value={value} onChange={event => onChange(event.target.value)} disabled={disabled}>
      {workflows.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
    </select>
    <p className="muted">{workflow?.description}</p>
    <h3>Required team</h3>
    <div className="team" aria-label="Required workflow roles">
      {workflow?.required_roles.map((name, index) => {
        const role = roles.find(item => item.name === name);
        const tip = `${id}-role-${index}`;
        return <span className="role-chip" tabIndex={0} key={name} aria-describedby={tip}>
          {name}<span role="tooltip" id={tip}>
            {role?.description || 'Role metadata unavailable'}<br />
            Model: {role?.model || 'Not configured (backend default)'}<br />
            Reasoning: {role?.reasoning_effort || 'Not configured (backend default)'}
          </span>
        </span>;
      })}
      {workflow && !workflow.required_roles.length && <small>No agents required.</small>}
    </div>
    <p className="muted">Existing agents are reused. Only missing required roles are started.</p>
    {workflow?.publication_policy === 'optional' && <>
      <label className="check"><input type="checkbox" checked={publish} disabled={disabled} onChange={event => onPublish(event.target.checked)} /> Publish approved changes to a PR/MR</label>
      <p className="publication">{publish ? 'This run can push commits and create a draft PR/MR, then check CI and deployment.' : 'Local implementation and review. PR/MR publication is disabled.'}</p>
    </>}
    {workflow?.publication_policy === 'required' && <p className="publication">Publication required: this workflow reviews and pushes committed changes to an existing or new PR/MR, then monitors and repairs CI. It can publish without another browser confirmation.</p>}
    {workflow?.publication_policy === 'none' && <p className="muted">This workflow has no PR/MR publication step.</p>}
  </section>;
}
