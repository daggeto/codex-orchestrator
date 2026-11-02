import { Textarea } from './ui/primitives/textarea';
import { FieldSet } from './ui/primitives/field';
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
  attachmentLocked?: boolean; acceptedAttachmentDraft?: string;
  label: string; placeholder?: string; submitLabel?: string;
  loading?: boolean; disabled?: boolean; submitDisabled?: boolean; submitReason?: string; error?: string;
  toolbar?: ReactNode; children?: ReactNode; rows?: number; hint?: string; resetKey?: number;
}
export function Chat({ attachmentLocked = false, acceptedAttachmentDraft, attachmentContext, draftKey, messages, onSubmit, label, placeholder, submitLabel = 'Send', loading = false, disabled = false, submitDisabled = false, submitReason, error, toolbar, children, resetKey, rows = 5, hint = 'Use multiple lines for context. Submit with the button below.' }: Props) {
  const id = useId();
  const attachments = useAttachments(`${draftKey || id}:attachments`, attachmentContext, attachmentLocked);
  const [draft, setDraft] = useState(() => { try { return draftKey ? sessionStorage.getItem(draftKey) || '' : ''; } catch { return ''; } });
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState('');
  const lock = useRef(false);
  const version = useRef(0);
  const [durable, setDurable] = useState(true);
  useEffect(() => { if (resetKey !== undefined) setDraft(''); setFailure(''); }, [resetKey]);
  useEffect(() => { if (draftKey) { try { sessionStorage.setItem(draftKey, draft); } catch { setDurable(false); } } }, [draftKey, draft]);
  useEffect(() => { if (acceptedAttachmentDraft) attachments.clear(acceptedAttachmentDraft); }, [acceptedAttachmentDraft]);
  const blocked = loading || sending || attachments.submitted;
  const cannotSubmit = disabled || submitDisabled || blocked;
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
      attachments.clear(attachments.payload.draft_id);
    }}>Open accepted task</Link> to view its progress and files.</p>}
    <form onDragOver={event => { if (attachmentContext && event.dataTransfer.types.includes('Files')) event.preventDefault(); }} onDrop={event => { if (attachmentContext && event.dataTransfer.files.length) { event.preventDefault(); if (!blocked && !attachmentLocked) void attachments.select(event.dataTransfer.files); } }} onSubmit={async event => {
      event.preventDefault();
      if (cannotSubmit || lock.current || !attachments.ready || !(draft.trim() || attachments.files.length)) return;
      lock.current = true; setSending(true); setFailure('');
      const submitted = { text: draft.trim(), files: attachments.payload, version: version.current, draftId: attachments.payload.draft_id };
      try { await onSubmit(submitted.text, submitted.files); if (version.current === submitted.version) setDraft(''); attachments.clear(submitted.draftId); }
      catch (reason) { setFailure(reason instanceof Error ? reason.message : 'Message failed'); }
      finally { lock.current = false; setSending(false); }
    }}>
      {toolbar && <FieldSet disabled={loading || sending} aria-label="Execution settings">{toolbar}</FieldSet>}
      <label htmlFor={id}>{label}</label>
      <Textarea id={id} rows={rows} value={draft} onChange={event => { version.current += 1; setDraft(event.target.value); }}
        placeholder={placeholder} disabled={attachments.submitted} aria-describedby={hint ? `${id}-hint` : undefined} required={!attachments.files.length} />
      {attachmentContext && <AttachmentPicker controller={attachments} disabled={blocked || attachmentLocked} />}
      <div className="chat-actions">{hint && <small id={`${id}-hint`}>{hint}</small>}
        <Button variant="primary" type="submit" disabled={cannotSubmit || !attachments.ready || !(draft.trim() || attachments.files.length)}>{loading || sending ? 'Sending…' : submitLabel}</Button></div>
      {submitReason && <p role="status">{submitReason}</p>}
      {!durable && <p role="alert">Draft storage is unavailable. Keep this page open; changes are held only in memory.</p>}
      {shownError && <p className="error" role="alert">{shownError}</p>}
    </form>
    {children}
  </div>;
}
