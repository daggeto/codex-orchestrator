import { useOperation } from '../useOperation';
import type { AttachmentSubmission } from '../types';
import { RadioGroup, RadioGroupItem } from './ui/primitives/radio-group';
import { NativeSelect } from './ui/primitives/native-select';
import { Textarea } from './ui/primitives/textarea';
import { Input } from './ui/primitives/input';
import { FieldSet } from './ui/primitives/field';
import { useEffect, useRef, useState } from 'react';
import { ApiError, post } from '../api';
import type { InputRequest, TaskMessage } from '../types';
import { useAttachments } from '../useAttachments';
import type { AttachmentContext } from '../useAttachments';
import { AttachmentPicker } from './AttachmentPicker';
import { Button } from './ui/Button';
type Question = { id: string; question?: string; header?: string; options?: { label: string; description?: string }[] };
function questions(input: InputRequest): Question[] {
  return input.kind === 'backend_input' ? input.options.filter((value): value is Question => !!value && typeof value === 'object' && typeof (value as Question).id === 'string') : [];
}
export function PendingInput({ input, messages = [], attachmentContext, inactive = false, disabled = false, onDismiss, onResolved }: { input: InputRequest; messages?: TaskMessage[]; attachmentContext?: AttachmentContext; inactive?: boolean; disabled?: boolean; onDismiss?: () => void; onResolved?: () => void }) {
  const draftKey = `input-response:${input.request_id}`;
  const attachments = useAttachments(`${draftKey}:attachments`, attachmentContext);
  const [draft] = useState<{ response?: string; answers?: Record<string, string> }>(() => { try { return JSON.parse(sessionStorage.getItem(draftKey) || '{}'); } catch { return {}; } });
  const [response, setResponse] = useState(draft?.response || '');
  const [answers, setAnswers] = useState<Record<string, string>>(draft?.answers || {});
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [resolved, setResolved] = useState(false);
  const lock = useRef(false);
  const operation = useOperation<AttachmentSubmission & { response: unknown }>(`question:${input.request_id}`, 'question');
  const accepted = resolved || operation.current?.status === 'accepted';
  const [durable, setDurable] = useState(true);
  useEffect(() => { try { if (!resolved) sessionStorage.setItem(draftKey, JSON.stringify({ response, answers })); } catch { setDurable(false); } }, [draftKey, response, answers, resolved]);
  useEffect(() => {
    const attempt = operation.current;
    if (!attempt?.request || !operation.unresolved) return;
    const matching = messages.some(raw => {
      const message = raw as TaskMessage & { input_request_id?: string; response?: unknown };
      return message.input_request_id === input.request_id && JSON.stringify(message.response) === JSON.stringify(attempt.request?.response) && JSON.stringify(message.attachment_ids) === JSON.stringify(attempt.request?.attachment_ids);
    });
    if (matching) operation.settle(attempt, 'accepted');
  }, [messages, operation.current, input.request_id]);
  const items = questions(input);
  const ready = items.length ? items.every(q => answers[q.id]?.trim()) : !!response.trim();
  async function send(replay = false) {
    if (lock.current || accepted || disabled || (!replay && (inactive || operation.unresolved || !ready || !attachments.ready))) return;
    lock.current = true; setPending(true); setError('');
    const responseValue = items.length ? { answers: Object.fromEntries(items.map(q => [q.id, { answers: [answers[q.id]] }])) } : response;
    let attempt;
    try {
      attempt = replay ? operation.current : operation.prepare({ response: responseValue, ...attachments.payload });
      if (!attempt?.request) return;
      // The request-specific response endpoint deduplicates this exact content.
      await post(`/api/input-requests/${encodeURIComponent(input.request_id)}/resolve`, attempt.request);
      operation.settle(attempt, 'accepted'); attachments.clear(); setResolved(true);
      try { sessionStorage.removeItem(draftKey); } catch { setDurable(false); }
      onResolved?.();
    } catch (reason) {
      if (attempt) operation.settle(attempt, 'uncertain', undefined, String(reason));
      setError(reason instanceof ApiError && reason.status === 409 ? 'This request expired or was answered elsewhere. Your response is retained. Check the saved response before dismissing it.' : `${String(reason)}. Acceptance is unconfirmed; your exact response is saved.`);
    } finally { lock.current = false; setPending(false); }
  }
  return <form className="pending-input" aria-busy={pending} onSubmit={event => {
    event.preventDefault(); void send();
  }}><h2>{input.kind === 'approval' ? 'Approval needed' : 'Waiting for you'}</h2>
    {disabled && <p role="status">Task state is stale. Refresh the task before sending; your response remains editable.</p>}
    {inactive && <p role="status">This request is no longer active. It expired or was answered elsewhere. Your response is retained for reference.</p>}
    <FieldSet disabled={pending || accepted || inactive}>{items.length ? items.map(q => <FieldSet key={q.id}><legend>{q.header}{q.header && ': '}{q.question || 'Your response'}</legend>
      <RadioGroup aria-label={q.question || q.header || 'Your response'} name={`${input.request_id}:${q.id}`} value={answers[q.id] || ''} onValueChange={value => setAnswers(previous => ({ ...previous, [q.id]: String(value) }))}>{Array.isArray(q.options) && q.options.map((option, index) => <label className="input-option" key={index}><RadioGroupItem value={option.label} /><span>{option.label}{option.description && <small>{option.description}</small>}</span></label>)}</RadioGroup>
      <label>Write a response<Input type="text" value={answers[q.id] || ''} onChange={event => setAnswers(previous => ({ ...previous, [q.id]: event.target.value }))} /></label>
    </FieldSet>) : <><p>{input.prompt}</p>{input.kind === 'approval' ? <label>Decision<NativeSelect required value={response} onChange={event => setResponse(event.target.value)}><option value="">Choose…</option><option value="approved">Approve</option><option value="rejected">Reject</option></NativeSelect></label> : <label>Your response<Textarea required value={response} onChange={event => setResponse(event.target.value)} /></label>}</>}
    {attachmentContext && <AttachmentPicker controller={attachments} disabled={pending || accepted || inactive} />}
    <Button type="submit" variant="primary" disabled={pending || accepted || inactive || disabled || operation.unresolved || !ready || !attachments.ready}>{accepted ? 'Answered' : pending ? 'Sending…' : 'Send response'}</Button></FieldSet>{operation.unresolved && <Button disabled={pending || disabled} onClick={() => void send(true)}>Check saved response</Button>}{(!durable || !operation.durable) && <p role="alert">Storage is unavailable. Your response is retained only while this page stays open.</p>}{inactive && <Button onClick={onDismiss}>Dismiss saved response</Button>}{error && <p role="alert" className="error">{error}</p>}
  </form>;
}
