import { useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { Dialog } from '@base-ui/react/dialog';
import { Button } from './ui/Button';
import { captureFeedbackPage } from '../feedbackCapture';
import type { FeedbackCapture } from '../feedbackCapture';
import { post } from '../api';

export function FeedbackWidget() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [situation, setSituation] = useState('');
  const [improvements, setImprovements] = useState('');
  const [capture, setCapture] = useState<FeedbackCapture>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<Record<string, unknown> | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  async function start() {
    if (pending.current) { setOpen(true); return; }
    setBusy(true); setError('');
    try { setCapture(await captureFeedbackPage()); }
    catch (reason) { setCapture(undefined); setError(String(reason)); }
    finally { setBusy(false); setOpen(true); }
  }
  async function submit() {
    if (!capture || busy) return;
    setBusy(true); setError('');
    pending.current ||= { situation, improvements, ...capture, idempotency_key: crypto.randomUUID() };
    try {
      const result = await post<{ feedback_id: string }>('/api/feedback', pending.current, 60000);
      pending.current = null; setSituation(''); setImprovements(''); setCapture(undefined); setOpen(false);
      navigate(`/feedback/${result.feedback_id}`);
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }
  return <div data-feedback-widget>
    <Button ref={trigger} className="feedback-float" disabled={busy} onClick={() => void start()}>{busy ? 'Capturing…' : 'Feedback'}</Button>
    <Dialog.Root open={open} onOpenChange={value => { if (!busy) setOpen(value); }}>
      <Dialog.Portal><Dialog.Backdrop className="feedback-backdrop" />
        <Dialog.Popup className="feedback-modal" finalFocus={trigger}>
          <Dialog.Title>Share feedback</Dialog.Title>
          <Dialog.Description>Describe what happened and what would help. This feedback proposes changes to Orch.</Dialog.Description>
          <form onSubmit={event => { event.preventDefault(); void submit(); }}>
            <label>Situation<textarea required maxLength={20000} value={situation} disabled={busy || !!pending.current} onChange={event => setSituation(event.target.value)} /></label>
            <label>Improvements found or wanted<textarea required maxLength={20000} value={improvements} disabled={busy || !!pending.current} onChange={event => setImprovements(event.target.value)} /></label>
            {capture && <details><summary>Attached page context · {capture.capture.route}</summary><img className="feedback-preview" src={`data:image/png;base64,${capture.screenshot}`} alt="Page captured before opening this form" /><p>Screenshot, HTML snapshot, and saved task/session context will be included.</p></details>}
            {error && <p role="alert">{error}</p>}
            {!capture && <Button disabled={busy} onClick={() => { setOpen(false); void start(); }}>Retry page capture</Button>}
            <div className="feedback-actions"><Button disabled={busy} onClick={() => setOpen(false)}>Close</Button>
              <Button type="submit" variant="primary" disabled={busy || !capture || !situation.trim() || !improvements.trim()}>{busy ? 'Submitting…' : pending.current ? 'Retry submission' : 'Submit feedback'}</Button></div>
          </form>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  </div>;
}
