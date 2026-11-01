import { useEffect, useRef, useState } from 'react';
import { ApiError, post } from '../api';
import type { InputRequest } from '../types';
import { useAttachments } from '../useAttachments';
import type { AttachmentContext } from '../useAttachments';
import { AttachmentPicker } from './AttachmentPicker';
import { Button } from './ui/Button';
type Question = { id: string; question?: string; header?: string; options?: { label: string; description?: string }[] };
function questions(input: InputRequest): Question[] {
  return input.kind === 'backend_input' ? input.options.filter((value): value is Question => !!value && typeof value === 'object' && typeof (value as Question).id === 'string') : [];
}
export function PendingInput({ input, attachmentContext, inactive = false, onDismiss, onResolved }: { input: InputRequest; attachmentContext?: AttachmentContext; inactive?: boolean; onDismiss?: () => void; onResolved?: () => void }) {
  const draftKey = `input-response:${input.request_id}`;
  const attachments = useAttachments(`${draftKey}:attachments`, attachmentContext);
  const [draft] = useState<{ response?: string; answers?: Record<string, string> }>(() => { try { return JSON.parse(sessionStorage.getItem(draftKey) || '{}'); } catch { return {}; } });
  const [response, setResponse] = useState(draft?.response || '');
  const [answers, setAnswers] = useState<Record<string, string>>(draft?.answers || {});
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [resolved, setResolved] = useState(false);
  const lock = useRef(false);
  useEffect(() => { try { if (!resolved) sessionStorage.setItem(draftKey, JSON.stringify({ response, answers })); } catch { /* Keep the in-memory draft. */ } }, [draftKey, response, answers, resolved]);
  const items = questions(input);
  const ready = items.length ? items.every(q => answers[q.id]?.trim()) : !!response.trim();
  return <form className="pending-input" aria-busy={pending} onSubmit={async event => {
    event.preventDefault(); if (lock.current || resolved || inactive || !ready || !attachments.ready) return;
    lock.current = true; setPending(true); setError('');
    const value = items.length ? { answers: Object.fromEntries(items.map(q => [q.id, { answers: [answers[q.id]] }])) } : response;
    try { await post(`/api/input-requests/${encodeURIComponent(input.request_id)}/resolve`, { response: value, ...attachments.payload }); attachments.clear(); setResolved(true); try { sessionStorage.removeItem(draftKey); } catch { /* Storage is optional. */ } onResolved?.(); }
    catch (reason) { setError(reason instanceof ApiError && reason.status === 409 && reason.message.includes('no longer active') ? 'This request has expired or was already answered. Check the latest task state; your response is kept here.' : String(reason)); }
    finally { lock.current = false; setPending(false); }
  }}><h2>{input.kind === 'approval' ? 'Approval needed' : 'Waiting for you'}</h2>
    {inactive && <p role="status">This request is no longer active. It expired or was answered elsewhere. Your response is retained for reference.</p>}
    <fieldset disabled={pending || resolved || inactive}>{items.length ? items.map(q => <fieldset key={q.id}><legend>{q.header}{q.header && ': '}{q.question || 'Your response'}</legend>
      {Array.isArray(q.options) && q.options.map((option, index) => <label className="input-option" key={index}><input type="radio" name={`${input.request_id}:${q.id}`} value={option.label} checked={answers[q.id] === option.label} onChange={() => setAnswers(previous => ({ ...previous, [q.id]: option.label }))} /><span>{option.label}{option.description && <small>{option.description}</small>}</span></label>)}
      <label>Write a response<input type="text" value={answers[q.id] || ''} onChange={event => setAnswers(previous => ({ ...previous, [q.id]: event.target.value }))} /></label>
    </fieldset>) : <><p>{input.prompt}</p>{input.kind === 'approval' ? <label>Decision<select required value={response} onChange={event => setResponse(event.target.value)}><option value="">Choose…</option><option value="approved">Approve</option><option value="rejected">Reject</option></select></label> : <label>Your response<textarea required value={response} onChange={event => setResponse(event.target.value)} /></label>}</>}
    {attachmentContext && <AttachmentPicker controller={attachments} disabled={pending || resolved || inactive} />}
    <Button type="submit" variant="primary" disabled={pending || resolved || inactive || !ready || !attachments.ready}>{resolved ? 'Answered' : pending ? 'Sending…' : 'Send response'}</Button></fieldset>{inactive && <Button onClick={onDismiss}>Dismiss saved response</Button>}{error && <p role="alert" className="error">{error}</p>}
  </form>;
}
