import { Link } from 'react-router';
import type { StepExecution } from '../types';
export function WorkflowTimeline({ steps }: { steps: StepExecution[] }) {
  return <ol className="workflow-timeline">{steps.map(step => <li key={step.execution_id}>
    <strong>{step.step_id}</strong> · attempt {step.attempt} · {step.status}
    {step.agent_id && <> · <Link to={`/agents/${step.agent_id}`}>Agent</Link></>}
    {step.parent_id && <small>Branch of {steps.find(parent => parent.execution_id === step.parent_id)?.step_id || step.parent_id}</small>}
    {step.dependencies.length > 0 && <small>After {step.dependencies.map(id => steps.find(item => item.execution_id === id)?.step_id || id).join(', ')}</small>}
    <details><summary>Execution result</summary><pre>{JSON.stringify(step.output, null, 2)}</pre></details>
  </li>)}</ol>;
}
