import { Link } from 'react-router';
import { Badge } from './ui/Badge';
import type { StepExecution } from '../types';
export function WorkflowTimeline({ steps, taskId }: { steps: StepExecution[]; taskId?: string }) {
  return <ol className="workflow-timeline">{steps.map(step => <li key={step.execution_id}>
    <div className="step-heading"><strong>{step.step_id}</strong><Badge>{step.status}</Badge><small>Attempt {step.attempt}</small></div>
    {step.agent_id && <> · <Link to={`/agents/${step.agent_id}${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''}`}>Agent</Link></>}
    {step.parent_id && <small>Branch of {steps.find(parent => parent.execution_id === step.parent_id)?.step_id || step.parent_id}</small>}
    {step.dependencies.length > 0 && <small>After {step.dependencies.map(id => steps.find(item => item.execution_id === id)?.step_id || id).join(', ')}</small>}
    <details><summary>Execution result</summary><pre>{JSON.stringify(step.output, null, 2)}</pre></details>
  </li>)}</ol>;
}
