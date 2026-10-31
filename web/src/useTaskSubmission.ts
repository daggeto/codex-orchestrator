import { useEffect, useRef, useState } from 'react';
import { ApiError, get, post } from './api';
import type { TaskSubmission, WorkflowRun } from './types';

const storageKey = 'orchestrator.task.v1';
export const terminalRun = (run?: WorkflowRun) => !!run && ['completed', 'failed', 'cancelled', 'interrupted', 'paused'].includes(run.status);
function restore(): TaskSubmission | null {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || 'null');
    return value && typeof value.run_id === 'string' && typeof value.workflow === 'string' &&
      typeof value.variables?.cwd === 'string' && typeof value.variables?.task_description === 'string' ? value : null;
  } catch { return null; }
}
export function useTaskSubmission() {
  const [submission, setSubmission] = useState<TaskSubmission | null>(restore);
  const [run, setRun] = useState<WorkflowRun>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [rejected, setRejected] = useState(false);
  const lock = useRef(false);

  useEffect(() => {
    if (!submission || rejected) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const result = await get<{ run: WorkflowRun }>(`/api/workflows/runs/${encodeURIComponent(submission.run_id)}`, controller.signal);
        if (controller.signal.aborted) return;
        setRun(result.run); setError(''); setRejected(false);
        if (terminalRun(result.run)) return;
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(reason instanceof ApiError && reason.status === 404
          ? 'Acceptance is unconfirmed. Retry the same submission to recover safely.'
          : 'Run status unavailable. Tracking will reconnect automatically.');
      }
      timer = setTimeout(() => { void refresh(); }, 2000);
    };
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [submission, rejected]);

  const send = async (next: TaskSubmission) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setRejected(false);
    try {
      // Persist the exact payload before sending, so retries retain identity even
      // if the acceptance response is lost or the browser is closed.
      localStorage.setItem(storageKey, JSON.stringify(next));
      setSubmission(next);
      await post(`/api/workflows/${encodeURIComponent(next.workflow)}/submit`, { run_id: next.run_id, variables: next.variables });
    } catch (reason) {
      const definite = reason instanceof ApiError && reason.status >= 400 && reason.status < 500;
      setRejected(definite);
      setError(reason instanceof Error ? reason.message : 'Submission failed');
      throw reason;
    } finally { lock.current = false; setBusy(false); }
  };
  const reset = () => {
    try { localStorage.removeItem(storageKey); }
    catch { setError('Browser storage is unavailable; unable to clear the saved submission.'); return; }
    setSubmission(null); setRun(undefined); setError(''); setRejected(false);
  };
  return { submission, run, busy, error, rejected, send, reset };
}
