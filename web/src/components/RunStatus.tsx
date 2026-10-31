import type { TaskSubmission, WorkflowRun } from '../types';

export function RunStatus({ submission, run, error, rejected = false }: { submission: TaskSubmission; run?: WorkflowRun; error: string; rejected?: boolean }) {
  const state = run?.state;
  const result = state?.final_variables;
  return <section className="run-status" aria-label="Workflow run">
    <div className="section-title"><strong>{submission.workflow}</strong><span className="status-pill" role="status">{run?.status || (rejected ? 'Submission rejected' : 'Confirming acceptance')}</span></div>
    <p className="muted">Run <code>{submission.run_id}</code></p>
    <p><code>{submission.variables.cwd}</code></p>
    <p role="status">{typeof state?.phase === 'string' ? state.phase.replaceAll('_', ' ') : rejected ? 'Review the error before starting another task.' : 'Waiting for service acknowledgement'}
      {typeof state?.current_step === 'string' && ` · ${state.current_step}`}
      {typeof state?.iteration === 'number' && ` · step execution ${state.iteration}`}</p>
    {typeof state?.error === 'string' && <p className="error" role="alert">{state.error}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {result !== undefined && <details open={run?.status === 'completed'}><summary>Saved result and outputs</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
    <p className="muted">Execution belongs to the service. Leaving this page does not stop it.</p>
  </section>;
}
