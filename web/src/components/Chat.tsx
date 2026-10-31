import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { ChatMessage } from '../types';

interface Props {
  messages: ChatMessage[]; onSubmit: (message: string) => Promise<void>;
  label: string; placeholder?: string; submitLabel?: string;
  loading?: boolean; disabled?: boolean; error?: string;
  toolbar?: ReactNode; children?: ReactNode; rows?: number; hint?: string; resetKey?: number;
}
export function Chat({ messages, onSubmit, label, placeholder, submitLabel = 'Send', loading = false, disabled = false, error, toolbar, children, resetKey, rows = 5, hint = 'Use multiple lines for context. Submit with the button below.' }: Props) {
  const id = useId();
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState('');
  const lock = useRef(false);
  useEffect(() => { setDraft(''); setFailure(''); }, [resetKey]);
  const blocked = disabled || loading || sending;
  // A caller managing recovery can supply an empty error to clear transport failures.
  const shownError = error ?? failure;
  return <div className="chat" aria-busy={loading || sending}>
    <div className="chat-transcript" role="log" aria-label={`${label} transcript`} aria-live="polite" aria-relevant="additions text" tabIndex={0}>
      {messages.map(message => <article className={`chat-message ${message.role}`} key={message.id}>
        <strong>{message.role === 'user' ? 'You' : message.role === 'assistant' ? 'Agent' : 'Orchestrator'}</strong>
        <p>{message.content}</p>
      </article>)}
    </div>
    <form onSubmit={async event => {
      event.preventDefault();
      if (blocked || lock.current || !draft.trim()) return;
      lock.current = true; setSending(true); setFailure('');
      try { await onSubmit(draft.trim()); setDraft(''); }
      catch (reason) { setFailure(reason instanceof Error ? reason.message : 'Message failed'); }
      finally { lock.current = false; setSending(false); }
    }}>
      {toolbar}
      <label htmlFor={id}>{label}</label>
      <textarea id={id} rows={rows} value={draft} onChange={event => setDraft(event.target.value)}
        placeholder={placeholder} disabled={blocked} aria-describedby={hint ? `${id}-hint` : undefined} required />
      <div className="chat-actions">{hint && <small id={`${id}-hint`}>{hint}</small>}
        <button className="primary" type="submit" disabled={blocked || !draft.trim()}>{loading || sending ? 'Sending…' : submitLabel}</button></div>
      {shownError && <p className="error" role="alert">{shownError}</p>}
    </form>
    {children}
  </div>;
}
