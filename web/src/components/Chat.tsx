import { useId, useRef, useState } from 'react';
import type { ChatMessage } from '../types';

interface Props {
  messages: ChatMessage[]; onSubmit: (message: string) => Promise<void>;
  label: string; placeholder?: string; submitLabel?: string;
  loading?: boolean; disabled?: boolean; error?: string;
}
export function Chat({ messages, onSubmit, label, placeholder, submitLabel = 'Send', loading = false, disabled = false, error }: Props) {
  const id = useId();
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState('');
  const lock = useRef(false);
  const blocked = disabled || loading || sending;
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
      <label htmlFor={id}>{label}</label>
      <textarea id={id} rows={5} value={draft} onChange={event => setDraft(event.target.value)}
        placeholder={placeholder} disabled={blocked} aria-describedby={`${id}-hint`} required />
      <div className="chat-actions"><small id={`${id}-hint`}>Use multiple lines for context. Submit with the button below.</small>
        <button className="primary" type="submit" disabled={blocked || !draft.trim()}>{loading || sending ? 'Sending…' : submitLabel}</button></div>
      {(error || failure) && <p className="error" role="alert">{error || failure}</p>}
    </form>
  </div>;
}
