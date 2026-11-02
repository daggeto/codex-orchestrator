import { useOperation } from '../useOperation';
import { Textarea } from './ui/primitives/textarea';
import { FieldSet } from './ui/primitives/field';
import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router';
import { post } from '../api';
import type { ScopeSplit, ScopeSuggestion } from '../types';
import { Button } from './ui/Button';
import { MessageContent } from './MessageContent';

export function ScopeSuggestions({ taskId, split, disabled, onStarted }: {
  taskId: string; split: ScopeSplit; disabled: boolean; onStarted: () => void;
}) {
  return <section className="execution-details scope-suggestions" aria-label="Suggested task split">
    <h2>Split this request into tasks</h2>
    <p>Keep the smaller change in the requested workflow and start broader work separately. Choose which tasks to start.</p>
    <p>New tasks start in separate worktrees from repository Main{split.destination_path && <> at <code>{split.destination_path}</code></>}. Source changes are not copied automatically. Available source attachments are copied; unavailable files block creation. Publication is disabled.</p>
    {split.source_path && <p>Source context: <code>{split.source_path}</code>{split.source_branch && <> · <code>{split.source_branch}</code></>}</p>}
    {split.source_commit && <p>Source commit: <code>{split.source_commit}</code></p>}
    {split.error && <p role="alert" className="error">The saved report has no valid task split: {split.error}. Review the agent report before creating a task manually.</p>}
    {!split.can_start && !!split.suggestions.length && <p>Starting suggestions requires a stopped, unarchived source task and an active repository Main workspace.</p>}
    {(['keep', 'separate'] as const).map(disposition => {
      const items = split.suggestions.filter(item => item.disposition === disposition);
      return items.length ? <div key={disposition}>
        <h3>{disposition === 'keep' ? 'Fits the requested workflow' : 'Suggested separate work'}</h3>
        {items.map(item => <Suggestion key={`${split.run_id}:${item.id}`} taskId={taskId} runId={split.run_id}
          item={item} disabled={disabled || !split.can_start} onStarted={onStarted} />)}
      </div> : null;
    })}
  </section>;
}

function Suggestion({ taskId, runId, item, disabled, onStarted }: {
  taskId: string; runId: string; item: ScopeSuggestion; disabled: boolean; onStarted: () => void;
}) {
  const prefix = useId();
  const draftKey = `scope-answers:${taskId}:${runId}:${item.id}`;
  const [answers, setAnswers] = useState<string[]>(() => { try { const saved = JSON.parse(sessionStorage.getItem(draftKey) || 'null'); return Array.isArray(saved) && saved.length === item.required_inputs.length && saved.every(value => typeof value === 'string') ? saved : item.required_inputs.map(() => ''); } catch { return item.required_inputs.map(() => ''); } });
  const operation = useOperation<{ run_id: string; answers: string[] }>(`scope:${taskId}:${runId}:${item.id}`, 'scope-suggestion');
  const [durable, setDurable] = useState(true);
  useEffect(() => { try { sessionStorage.setItem(draftKey, JSON.stringify(answers)); } catch { setDurable(false); } }, [draftKey, answers]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [accepted, setAccepted] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const lock = useRef(false);
  const created = item.task_id || accepted || (operation.current?.status === 'accepted' ? operation.current.result?.task_id : null);
  async function start() {
    if (lock.current || disabled || created) return;
    lock.current = true; setBusy(true); setError('');
    const attempt = operation.unresolved ? operation.current! : operation.prepare({ run_id: runId, answers });
    try {
      const result = await post<{ task_id: string }>(
        `/api/tasks/${encodeURIComponent(taskId)}/scope-suggestions/${item.id}/start`, attempt.request);
      if (!result.task_id) throw new Error('The service did not return the new task link.');
      operation.settle(attempt, 'accepted', { task_id: result.task_id }); setAccepted(result.task_id); setUncertain(false); onStarted();
    } catch (reason) {
      operation.settle(attempt, 'uncertain', undefined, String(reason));
      setError(`${reason instanceof Error ? reason.message : 'Unable to start this task'}. Retry to recover the same task if it was accepted.`);
      setUncertain(true);
    } finally { lock.current = false; setBusy(false); }
  }
  return <article className="scope-suggestion">
    <h4>{item.title}</h4>
    <p>Workflow: <strong>{item.workflow}</strong></p>
    <p>{item.reason}</p>
    <details open><summary>Proposed prompt</summary><MessageContent text={item.prompt} /></details>
    <details><summary>Context and dependencies</summary><MessageContent text={item.context} /></details>
    {!!item.required_inputs.length && <FieldSet disabled={busy || !!created}>
      <legend>Information needed before starting</legend>
      {item.required_inputs.map((question, index) => <div key={index}>
        <label htmlFor={`${prefix}-${index}`}>{question}</label>
        <Textarea id={`${prefix}-${index}`} rows={3} maxLength={16000} required value={answers[index]}
          onChange={event => setAnswers(previous => previous.map((answer, position) => position === index ? event.target.value : answer))} />
      </div>)}
    </FieldSet>}
    {!item.required_inputs.length && <p>No additional information is required.</p>}
    {!item.available && !created && <p>The suggested workflow is currently unavailable.</p>}
    {(!durable || !operation.durable) && <p role="alert">Draft and request recovery are held only in memory. Keep this page open.</p>}
    {error && !created && <p role="alert" className="error">{error}</p>}
    {created ? <p role="status">Task created. <Link to={`/tasks/${encodeURIComponent(created)}`}>Open {item.title}</Link></p> :
      <Button variant="primary" disabled={disabled || busy || !item.available || answers.some(answer => !answer.trim())}
        onClick={() => void start()}>{busy ? 'Starting…' : uncertain || operation.unresolved ? 'Check saved task creation' : 'Start this task'}</Button>}
  </article>;
}
