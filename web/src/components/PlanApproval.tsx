import { Disclosure } from './ui/Disclosure';
import { useOperation } from '../useOperation';
import { NativeSelect } from './ui/primitives/native-select';
import { Textarea } from './ui/primitives/textarea';
import { useEffect, useRef, useState } from 'react';
import { ApiError, post } from '../api';
import { MessageContent } from './MessageContent';
import { Button } from './ui/Button';
import { planSections } from '../planSections';
export interface PlanApprovalState {
  id: string; revision: number; markdown: string; status: string; author: string; error?: string; snapshot?: string | null;
  actions?: Record<string, unknown>; targets: Record<string, string>; approved_revision?: number;
  messages: { role: string; text: string }[];
  revisions: { revision: number; markdown: string; author: string }[];
}
export function PlanApproval({ taskId, plan, disabled, refresh }: { taskId: string; plan: PlanApprovalState; disabled: boolean; refresh: () => void }) {
  const storage = `plan-draft:${plan.id}`;
  const [draft, setDraft] = useState(() => { try { return sessionStorage.getItem(storage) ?? plan.markdown ?? ''; } catch { return plan.markdown ?? ''; } });
  const [baseRevision, setBaseRevision] = useState(() => { try { const value = sessionStorage.getItem(`${storage}:revision`); return value === null ? (draft === plan.markdown ? plan.revision : -1) : Number(value); } catch { return plan.revision; } });
  const [editing, setEditing] = useState(draft !== (plan.markdown ?? ''));
  const [message, setMessage] = useState(() => { try { return sessionStorage.getItem(`${storage}:message`) || ''; } catch { return ''; } });
  const [target, setTarget] = useState(Object.keys(plan.targets || {})[0] || '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const operation = useOperation<Record<string, unknown>>(`plan:${taskId}:${plan.id}`, 'plan-action');
  const draftVersion = useRef(0);
  const [durable, setDurable] = useState(true);
  const conflict = editing && baseRevision !== plan.revision;
  useEffect(() => {
    if (!editing) { setDraft(plan.markdown); setBaseRevision(plan.revision); }
  }, [plan.markdown, plan.revision, editing]);
  useEffect(() => {
    const attempt = operation.current;
    if (attempt && operation.unresolved && plan.actions?.[attempt.id]) operation.settle(attempt, 'accepted');
  }, [plan.actions, operation.current]);
  const dirty = draft !== plan.markdown && editing;
  const inactive = disabled || busy || plan.status !== 'waiting';
  function update(value: string) { draftVersion.current += 1; setDraft(value); try { sessionStorage.setItem(storage, value); sessionStorage.setItem(`${storage}:revision`, String(baseRevision)); } catch { setDurable(false); } }
  async function action(action: string, replay = false) {
    if (lock.current || (!replay && (inactive || conflict || operation.unresolved))) return;
    const payload = { action, approval_id: plan.id, revision: baseRevision, ...(action === 'save' ? { markdown: draft } : {}), ...(['chat','send_back'].includes(action) ? { message } : {}), ...(action === 'send_back' ? { target } : {}) };
    lock.current = true; setBusy(true); setError('');
    let attempt;
    try {
      attempt = replay ? operation.current : operation.prepare(payload, draftVersion.current);
      if (!attempt?.request) return;
      const sent = attempt.request;
      await post(`/api/tasks/${encodeURIComponent(taskId)}/plan-approval/actions`, { ...sent, idempotency_key: attempt.id });
      const receipt = operation.settle(attempt, 'accepted');
      if (receipt.status !== 'accepted') return;
      if (sent.action === 'save' && attempt.draftVersion === draftVersion.current) {
        setEditing(false); try { sessionStorage.removeItem(storage); sessionStorage.removeItem(`${storage}:revision`); } catch { setDurable(false); }
      }
      if (sent.action === 'chat' || sent.action === 'send_back') { setMessage(''); try { sessionStorage.removeItem(`${storage}:message`); } catch { setDurable(false); } }
      refresh();
    } catch (reason) {
      if (attempt) operation.settle(attempt, reason instanceof ApiError && reason.status === 409 && reason.message.startsWith('Plan revision changed') ? 'rejected' : 'uncertain', undefined, String(reason));
      setError(String(reason)); refresh();
    } finally { lock.current = false; setBusy(false); }
  }
  return <section className="plan-approval" aria-label="Developer plan approval" aria-busy={busy}>
    <header><h2>{plan.status === 'approved' ? 'Approved plan' : 'Review the plan'}</h2><p>Revision {plan.revision || 0} · {plan.author} · {plan.status}</p></header>
    <p>Scan the section summaries and expand for details. Approval sends the complete saved plan to implementers.</p>
    {plan.error && <p role="alert" className="error">{plan.error}</p>}
    {conflict && <div role="alert" className="operation-warning"><p>The saved plan changed from revision {baseRevision} to {plan.revision}. Your edits are retained. Compare them with the current saved plan before continuing.</p><Button disabled={busy || operation.unresolved} onClick={() => { setBaseRevision(plan.revision); try { sessionStorage.setItem(`${storage}:revision`, String(plan.revision)); } catch { setDurable(false); } }}>Keep edits against revision {plan.revision}</Button><Button disabled={busy || operation.unresolved} onClick={() => { setDraft(plan.markdown); setBaseRevision(plan.revision); setEditing(false); try { sessionStorage.removeItem(storage); sessionStorage.removeItem(`${storage}:revision`); } catch { setDurable(false); } }}>Use saved plan</Button></div>}
    {operation.unresolved && <p role="status">A plan action needs reconciliation. <Button disabled={busy || disabled} onClick={() => void action('', true)}>Check saved plan action</Button></p>}
    {(!durable || !operation.durable) && <p role="alert">Storage is unavailable. Keep this page open to retain your draft and request identity.</p>}
    <Disclosure key={plan.status === 'approved' ? 'approved' : 'review'} title={plan.status === 'approved' ? 'Read approved plan' : 'Saved plan'} defaultOpen={plan.status !== 'approved'}>
    <section className="plan-document" aria-label="Saved plan">
      {plan.markdown ? <div key={`${plan.id}:${plan.revision}`}>
        {planSections(plan.markdown).map((section, index) => <Disclosure className="plan-section" key={index} title={<>
            <span className="plan-section-title">{section.title}</span>
            <span className="plan-section-preview"><strong>{section.hasTldr ? 'TL;DR' : 'Excerpt'}: </strong>{section.preview}</span>
            <span className="plan-section-toggle"><span className="plan-read-more">Read more</span><span className="plan-read-less">Read less</span></span>
          </>}>
          <MessageContent text={section.markdown} />
        </Disclosure>)}
        <Disclosure className="plan-full" title="Read full plan"><MessageContent text={plan.markdown} /></Disclosure>
      </div> : <p>Plan preparation needs attention. Send feedback to a planning agent to retry.</p>}
    </section>
    </Disclosure>
    {editing ? <><label>Plan Markdown<Textarea className="plan-editor" rows={22} value={draft} disabled={busy} onChange={event => update(event.target.value)} /></label><details><summary>Preview edits</summary><MessageContent text={draft} /></details>
      <Button disabled={inactive || conflict || operation.unresolved || !draft.trim()} onClick={() => void action('save')}>Save plan</Button>
      <Button disabled={busy} onClick={() => { setDraft(plan.markdown); setEditing(false); try { sessionStorage.removeItem(storage); } catch { /* Optional storage. */ } }}>Discard edits</Button>
      </> : <>
      {plan.status !== 'approved' && <Button disabled={busy} onClick={() => { setDraft(plan.markdown); setBaseRevision(plan.revision); setEditing(true); }}>Edit plan</Button>}</>}
    {plan.status !== 'approved' && <><p role="status">{dirty ? 'Unsaved changes. Save before discussing, sending back, or approving.' : 'Approval starts implementation of the complete saved revision, including collapsed details.'}</p>
      {!message.trim() && <p className="muted">Enter feedback to discuss the plan or send it back to a planning agent.</p>}
      <label>Discuss the plan or describe requested changes<Textarea rows={4} value={message} disabled={busy} onChange={event => { setMessage(event.target.value); try { sessionStorage.setItem(`${storage}:message`, event.target.value); } catch { /* Optional storage. */ } }} /></label>
      <Button disabled={inactive || conflict || operation.unresolved || dirty || !message.trim()} onClick={() => void action('chat')}>Discuss plan</Button>
      <label>Send back to<NativeSelect value={target} disabled={busy} onChange={event => setTarget(event.target.value)}>{Object.entries(plan.targets || {}).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</NativeSelect></label>
      <Button disabled={inactive || conflict || operation.unresolved || dirty || !message.trim() || !target} onClick={() => void action('send_back')}>Send back</Button>
      {!plan.snapshot && <p className="muted">Approval verifies the current checkout before implementation starts. Existing local edits are retained.</p>}
      <Button variant="primary" disabled={inactive || conflict || operation.unresolved || dirty || !plan.markdown?.trim()} onClick={() => void action('approve')}>Approve and implement</Button></>}
    {error && <p role="alert" className="error">{error} Your draft is retained.</p>}
    <details><summary>Plan discussion ({plan.messages?.length || 0})</summary>{plan.messages?.map((item,index) => <article key={index}><strong>{item.role}</strong><MessageContent text={item.text} /></article>)}</details>
    <details><summary>Revision history</summary>{plan.revisions?.map(revision => <details key={revision.revision}><summary>Revision {revision.revision} · {revision.author}</summary><MessageContent text={revision.markdown} /></details>)}</details>
  </section>;
}
