import { useRef, useState } from 'react';
import { post } from '../api';
import { MessageContent } from './MessageContent';
import { Button } from './ui/Button';
import { planSections } from '../planSections';
export interface PlanApprovalState {
  id: string; revision: number; markdown: string; status: string; author: string; error?: string; snapshot?: string | null;
  targets: Record<string, string>; approved_revision?: number;
  messages: { role: string; text: string }[];
  revisions: { revision: number; markdown: string; author: string }[];
}
export function PlanApproval({ taskId, plan, disabled, refresh }: { taskId: string; plan: PlanApprovalState; disabled: boolean; refresh: () => void }) {
  const storage = `plan-draft:${plan.id}`;
  const [draft, setDraft] = useState(() => { try { return sessionStorage.getItem(storage) ?? plan.markdown ?? ''; } catch { return plan.markdown ?? ''; } });
  const [editing, setEditing] = useState(draft !== (plan.markdown ?? ''));
  const [message, setMessage] = useState(() => { try { return sessionStorage.getItem(`${storage}:message`) || ''; } catch { return ''; } });
  const [target, setTarget] = useState(Object.keys(plan.targets || {})[0] || '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const identity = useRef<{ payload: string; key: string } | undefined>(undefined);
  const dirty = draft !== plan.markdown && editing;
  const inactive = disabled || busy || plan.status !== 'waiting';
  function update(value: string) { setDraft(value); try { sessionStorage.setItem(storage, value); } catch { /* Draft remains in memory. */ } }
  async function action(action: string) {
    if (lock.current) return;
    const payload = { action, approval_id: plan.id, revision: plan.revision, ...(action === 'save' ? { markdown: draft } : {}), ...(['chat','send_back'].includes(action) ? { message } : {}), ...(action === 'send_back' ? { target } : {}) };
    const serialized = JSON.stringify(payload);
    if (identity.current?.payload !== serialized) identity.current = { payload: serialized, key: crypto.randomUUID() };
    lock.current = true; setBusy(true); setError('');
    try {
      await post(`/api/tasks/${encodeURIComponent(taskId)}/plan-approval/actions`, { ...payload, idempotency_key: identity.current.key });
      if (action === 'save') { setEditing(false); try { sessionStorage.removeItem(storage); } catch { /* Optional storage. */ } }
      if (action === 'chat' || action === 'send_back') { setMessage(''); try { sessionStorage.removeItem(`${storage}:message`); } catch { /* Optional storage. */ } }
      identity.current = undefined;
      refresh();
    } catch (reason) { setError(String(reason)); }
    finally { lock.current = false; setBusy(false); }
  }
  return <section className="plan-approval" aria-label="Developer plan approval" aria-busy={busy}>
    <header><h2>{plan.status === 'approved' ? 'Approved plan' : 'Review the plan'}</h2><p>Revision {plan.revision || 0} · {plan.author} · {plan.status}</p></header>
    <p>Scan the section summaries and expand for details. Approval sends the complete saved plan to implementers.</p>
    {plan.error && <p role="alert" className="error">{plan.error}</p>}
    <section className="plan-document" aria-label="Saved plan">
      {plan.markdown ? <div key={`${plan.id}:${plan.revision}`}>
        {planSections(plan.markdown).map((section, index) => <details className="plan-section" key={index}>
          <summary>
            <span className="plan-section-title">{section.title}</span>
            <span className="plan-section-preview"><strong>{section.hasTldr ? 'TL;DR' : 'Excerpt'}: </strong>{section.preview}</span>
            <span className="plan-section-toggle"><span className="plan-read-more">Read more</span><span className="plan-read-less">Read less</span></span>
          </summary>
          <MessageContent text={section.markdown} />
        </details>)}
        <details className="plan-full"><summary>Read full plan</summary><MessageContent text={plan.markdown} /></details>
      </div> : <p>Plan preparation needs attention. Send feedback to a planning agent to retry.</p>}
    </section>
    {editing ? <><label>Plan Markdown<textarea className="plan-editor" rows={22} value={draft} disabled={inactive} onChange={event => update(event.target.value)} /></label><details><summary>Preview edits</summary><MessageContent text={draft} /></details>
      <Button disabled={inactive || !draft.trim()} onClick={() => void action('save')}>Save plan</Button>
      <Button disabled={inactive} onClick={() => { setDraft(plan.markdown); setEditing(false); try { sessionStorage.removeItem(storage); } catch { /* Optional storage. */ } }}>Discard edits</Button>
      </> : <>
      {plan.status !== 'approved' && <Button disabled={inactive} onClick={() => { if (!draft) update(plan.markdown); setEditing(true); }}>Edit plan</Button>}</>}
    {plan.status !== 'approved' && <><p role="status">{dirty ? 'Unsaved changes. Save before discussing, sending back, or approving.' : 'Approval starts implementation of the complete saved revision, including collapsed details.'}</p>
      {!message.trim() && <p className="muted">Enter feedback to discuss the plan or send it back to a planning agent.</p>}
      <label>Discuss the plan or describe requested changes<textarea rows={4} value={message} disabled={inactive} onChange={event => { setMessage(event.target.value); try { sessionStorage.setItem(`${storage}:message`, event.target.value); } catch { /* Optional storage. */ } }} /></label>
      <Button disabled={inactive || dirty || !message.trim()} onClick={() => void action('chat')}>Discuss plan</Button>
      <label>Send back to<select value={target} disabled={inactive} onChange={event => setTarget(event.target.value)}>{Object.entries(plan.targets || {}).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <Button disabled={inactive || dirty || !message.trim() || !target} onClick={() => void action('send_back')}>Send back</Button>
      {!plan.snapshot && <p className="muted">Approval verifies the current checkout before implementation starts. Existing local edits are retained.</p>}
      <Button variant="primary" disabled={inactive || dirty || !plan.markdown?.trim()} onClick={() => void action('approve')}>Approve and implement</Button></>}
    {error && <p role="alert" className="error">{error} Your draft is retained.</p>}
    <details><summary>Plan discussion ({plan.messages?.length || 0})</summary>{plan.messages?.map((item,index) => <article key={index}><strong>{item.role}</strong><MessageContent text={item.text} /></article>)}</details>
    <details><summary>Revision history</summary>{plan.revisions?.map(revision => <details key={revision.revision}><summary>Revision {revision.revision} · {revision.author}</summary><MessageContent text={revision.markdown} /></details>)}</details>
  </section>;
}
