import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import type { ChatMessage, AttachmentSubmission } from '../types';
import { Button } from './ui/Button';
import { useAttachments } from '../useAttachments';
import type { AttachmentContext } from '../useAttachments';
import { AttachmentPicker } from './AttachmentPicker';
import { MessageContent } from './MessageContent';

interface Props {
  draftKey?: string; messages: ChatMessage[]; onSubmit: (message: string, attachments: AttachmentSubmission) => Promise<void>;
  attachmentContext?: AttachmentContext;
  label: string; placeholder?: string; submitLabel?: string;
  loading?: boolean; disabled?: boolean; error?: string;
  toolbar?: ReactNode; children?: ReactNode; rows?: number; hint?: string; resetKey?: number;
}
export function Chat({ attachmentContext, draftKey, messages, onSubmit, label, placeholder, submitLabel = 'Send', loading = false, disabled = false, error, toolbar, children, resetKey, rows = 5, hint = 'Use multiple lines for context. Submit with the button below.' }: Props) {
  const id = useId();
  const attachments = useAttachments(`${draftKey || id}:attachments`, attachmentContext);
  const [draft, setDraft] = useState(() => { try { return draftKey ? sessionStorage.getItem(draftKey) || '' : ''; } catch { return ''; } });
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState('');
  const lock = useRef(false);
  useEffect(() => { if (resetKey !== undefined) setDraft(''); setFailure(''); }, [resetKey]);
  useEffect(() => { if (draftKey) { try { sessionStorage.setItem(draftKey, draft); } catch { /* Draft remains in memory. */ } } }, [draftKey, draft]);
  const blocked = disabled || loading || sending || attachments.submitted;
  // A caller managing recovery can supply an empty error to clear transport failures.
  const shownError = error ?? failure;
  return <div className="chat" aria-busy={loading || sending}>
    <div className="chat-transcript" role="log" aria-label={`${label} transcript`} aria-live="polite" aria-relevant="additions text" tabIndex={0}>
      {messages.map(message => <article className={`chat-message ${message.role}`} key={message.id}>
        <strong>{message.role === 'user' ? 'You' : message.role === 'assistant' ? 'Agent' : 'Orchestrator'}</strong>
        <MessageContent text={message.content} />
      </article>)}
    </div>
    {attachments.acceptedTaskId && <p role="status">Your request was accepted. <Link to={`/tasks/${encodeURIComponent(attachments.acceptedTaskId)}`} onClick={() => {
      setDraft(''); attachments.clear();
      try { if (draftKey) sessionStorage.removeItem(draftKey); } catch { /* Optional storage. */ }
    }}>Open accepted task</Link> to view its progress and files.</p>}
    <form onDragOver={event => { if (attachmentContext && event.dataTransfer.types.includes('Files')) event.preventDefault(); }} onDrop={event => { if (attachmentContext && event.dataTransfer.files.length) { event.preventDefault(); if (!blocked) void attachments.select(event.dataTransfer.files); } }} onSubmit={async event => {
      event.preventDefault();
      if (blocked || lock.current || !attachments.ready || !(draft.trim() || attachments.files.length)) return;
      lock.current = true; setSending(true); setFailure('');
      try { await onSubmit(draft.trim(), attachments.payload); setDraft(''); attachments.clear(); }
      catch (reason) { setFailure(reason instanceof Error ? reason.message : 'Message failed'); }
      finally { lock.current = false; setSending(false); }
    }}>
      {toolbar && <fieldset disabled={blocked} aria-label="Execution settings">{toolbar}</fieldset>}
      <label htmlFor={id}>{label}</label>
      <textarea id={id} rows={rows} value={draft} onChange={event => setDraft(event.target.value)}
        placeholder={placeholder} disabled={blocked} aria-describedby={hint ? `${id}-hint` : undefined} required={!attachments.files.length} />
      {attachmentContext && <AttachmentPicker controller={attachments} disabled={blocked} />}
      <div className="chat-actions">{hint && <small id={`${id}-hint`}>{hint}</small>}
        <Button variant="primary" type="submit" disabled={blocked || !attachments.ready || !(draft.trim() || attachments.files.length)}>{loading || sending ? 'Sending…' : submitLabel}</Button></div>
      {shownError && <p className="error" role="alert">{shownError}</p>}
    </form>
    {children}
  </div>;
}
