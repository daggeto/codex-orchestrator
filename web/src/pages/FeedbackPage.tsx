import { useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { useResource } from '../useResource';
import { post } from '../api';
import { MessageContent } from '../components/MessageContent';
import { Button } from '../components/ui/Button';

const categories: Record<string, string> = { dashboard_bug: 'Dashboard bug', dashboard_ux_ui: 'Dashboard UX/UI', new_feature: 'New feature', orchestrator_improvement: 'Orchestrator improvement' };
interface Analysis { title: string; tldr: string; requirement: string; frustration: string; evidence: string; uncertainties: string; category: string; rationale: string; brief: string; questions: string[] }
interface Implementation { task_id: string; status: string; phase: string; run_id: string | null; summary: string | null; workspace: { workspace_id: string; path: string; branch: string | null } | null }
interface Feedback { feedback_id: string; situation: string; improvements: string; status: string; created_at: string; context: Record<string, unknown>; revision: number; analysis: Analysis | null; error?: string; analysis_task_id?: string; implementation_task_id?: string; implementation?: Implementation; implementation_request?: unknown; target?: { workspace_id: string; path: string; branch: string | null }; target_error?: string; approved?: { analysis: Analysis; target: { path: string }; revision: number }; completed_at?: string; history: { revision: number; kind: string; contents: unknown }[] }
const labels: Record<string, string> = { submitted: 'Submitted', analysing: 'Analyst working', awaiting_input: 'Your answers needed', awaiting_review: 'Ready for review', failed: 'Analysis failed', interrupted: 'Analysis interrupted', cancelled: 'Cancelled', completed: 'Completed', implementing: 'Implementation working', implementation_failed: 'Implementation needs attention', implementation_interrupted: 'Implementation interrupted' };
export function FeedbackListPage() {
  const [params] = useSearchParams();
  const completed = params.get('tab') === 'completed';
  const resource = useResource<{ items: Feedback[] }>(`/api/feedback?tab=${completed ? 'completed' : 'active'}`);
  return <section><h1>Feedback Loop</h1><p>Feedback about Orch and the dashboard.</p>
    <nav className="task-tabs" aria-label="Feedback status"><Link to="/feedback" aria-current={!completed ? 'page' : undefined}>Active</Link><Link to="/feedback?tab=completed" aria-current={completed ? 'page' : undefined}>Completed</Link></nav>
    {resource.error && <p role="alert">{resource.error}</p>}
    {resource.loading && <p role="status">Loading feedback…</p>}
    {resource.data?.items.length === 0 && <p>No {completed ? 'completed' : 'active'} feedback yet. Use the floating Feedback button to share a situation.</p>}
    <ul className="feedback-list">{resource.data?.items.map(item => <li key={item.feedback_id}><Link to={`/feedback/${item.feedback_id}`}>{item.analysis?.title || item.situation.slice(0, 100)}</Link><span>{item.analysis && categories[item.analysis.category]}</span><span>{labels[item.status] || item.status}</span><time>{new Date(item.created_at).toLocaleString()}</time></li>)}</ul>
  </section>;
}
function FeedbackControls({ item, refresh }: { item: Feedback; refresh: () => void }) {
  const [draft, setDraft] = useState<Analysis | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<Record<string, unknown> | null>(null);
  async function act(action: string) {
    if (busy) return;
    pending.current ||= { action, revision: item.revision, request_id: crypto.randomUUID(), ...(action === 'edit' ? { analysis: draft } : action === 'approve' ? { target_workspace_id: item.target?.workspace_id } : { text }) };
    setBusy(true); setError('');
    try {
      await post(`/api/feedback/${item.feedback_id}/actions`, pending.current, 60000);
      pending.current = null; setDraft(null); setText(''); refresh();
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }
  if (['completed', 'cancelled'].includes(item.status)) return null;
  const locked = busy || !!pending.current;
  return <section aria-label="Feedback actions">
    {item.error && <p role="alert">{item.error}</p>}
    {item.status === 'awaiting_input' && <><h2>Questions for you</h2><ol>{item.analysis?.questions.map((question, index) => <li key={index}>{question}</li>)}</ol></>}
    {['awaiting_input', 'awaiting_review'].includes(item.status) && <>
      <label>{item.status === 'awaiting_input' ? 'Your answers' : 'Ask the analyst to change the brief'}<textarea className="feedback-editor" maxLength={20000} disabled={locked} value={text} onChange={event => setText(event.target.value)} /></label>
      <Button disabled={locked || !text.trim()} onClick={() => void act(item.status === 'awaiting_input' ? 'answer' : 'revise')}>{item.status === 'awaiting_input' ? 'Send answers' : 'Request revision'}</Button>
    </>}
    {item.status === 'awaiting_review' && <>
      <Button disabled={locked} onClick={() => setDraft(item.analysis ? { ...item.analysis } : null)}>Change brief or category</Button>
      {draft && <div>
        <label>Category<select disabled={locked} value={draft.category} onChange={event => setDraft({ ...draft, category: event.target.value })}>{Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Category rationale<textarea className="feedback-editor" disabled={locked} value={draft.rationale} onChange={event => setDraft({ ...draft, rationale: event.target.value })} /></label>
        <label>Implementation brief<textarea className="feedback-editor" disabled={locked} value={draft.brief} onChange={event => setDraft({ ...draft, brief: event.target.value })} /></label>
        <Button disabled={locked || !draft.brief.trim() || !draft.rationale.trim()} onClick={() => void act('edit')}>Save changes</Button>
      </div>}
      {item.target ? <p>Implementation target: <code>{item.target.path}</code>. Approval starts a planner and the default coding workflow in a new worktree. Changes stay there for review.</p> : <p role="alert">{item.target_error || 'Orch implementation target is unavailable.'}</p>}
      <Button variant="primary" disabled={locked || !!draft || !item.target} onClick={() => void act('approve')}>Approve and implement</Button>
    </>}
    {['submitted', 'failed', 'interrupted'].includes(item.status) && <Button disabled={locked} onClick={() => void act('retry')}>Retry analysis</Button>}
    {['implementation_failed', 'implementation_interrupted'].includes(item.status) && !item.implementation_task_id && <Button disabled={locked} onClick={() => void act('retry_implementation')}>Retry saved implementation submission</Button>}
    {['implementation_failed', 'implementation_interrupted'].includes(item.status) && item.implementation_task_id && <p>Open the implementation task below to inspect scope decisions, resolve blockers, or continue the saved workflow.</p>}
    <Button disabled={locked} onClick={() => void act('cancel')}>Cancel feedback</Button>
    {error && <p role="alert">{error}</p>}
    {!!pending.current && !busy && <div><Button onClick={() => void act(String(pending.current!.action))}>Retry same action</Button><Button onClick={() => { pending.current = null; setError(''); refresh(); }}>Reload current feedback</Button></div>}
  </section>;
}
export function FeedbackPage() {
  const { feedbackId } = useParams();
  const resource = useResource<Feedback>(`/api/feedback/${encodeURIComponent(feedbackId!)}`);
  const item = resource.data;
  const evidence = `/api/feedback/${encodeURIComponent(feedbackId!)}/evidence`;
  return <section><Link to="/feedback">← Feedback Loop</Link>{resource.error && <p role="alert">{resource.error}</p>}
    {!item ? <p>{resource.loading ? 'Loading feedback…' : 'Feedback unavailable.'}</p> : <>
      <h1>{item.analysis?.title || item.situation.slice(0, 100)}</h1><p role="status">{labels[item.status] || item.status}</p>
      {item.analysis_task_id && <Link to={`/tasks/${encodeURIComponent(item.analysis_task_id)}`}>Open analyst task</Link>}
      {item.analysis && <><p className="feedback-text">{item.analysis.tldr}</p><p>{categories[item.analysis.category]} · {item.analysis.rationale}</p>
        <details><summary>Feedback agent insights</summary>{(['requirement', 'frustration', 'evidence', 'uncertainties'] as const).map(field => <div key={field}><h2>{field[0].toUpperCase() + field.slice(1)}</h2><p className="feedback-text">{item.analysis![field]}</p></div>)}</details>
        <h2>Implementation brief</h2><p className="feedback-text">{item.analysis.brief}</p></>}
      {(item.implementation || item.implementation_task_id) && <section aria-label="Implementation progress"><h2>Implementation</h2>
        <Link to={`/tasks/${encodeURIComponent(item.implementation_task_id || item.implementation!.task_id)}`}>Open implementation task and recovery controls</Link>
        {item.implementation && <><p role="status">{item.implementation.phase}</p>
          {item.implementation.workspace && <p><Link to={`/workspaces/${encodeURIComponent(item.implementation.workspace.workspace_id)}`}>Open implementation worktree</Link> · <code>{item.implementation.workspace.branch}</code></p>}
          {item.implementation.summary && <MessageContent text={item.implementation.summary} />}
        </>}
        {item.status === 'completed' && <p>Implementation finished. Changes remain in the worktree for review. Merge and deployment are separate actions.</p>}
      </section>}
      {item.approved && <details><summary>Approved handoff · revision {item.approved.revision}</summary><p>Target: {item.approved.target.path}</p><p className="feedback-text">{item.approved.analysis.brief}</p></details>}
      <FeedbackControls key={`${item.feedback_id}:${item.revision}:${item.status}`} item={item} refresh={resource.refresh} />
      <details><summary>Original feedback and captured evidence</summary><h2>Situation</h2><p className="feedback-text">{item.situation}</p><h2>Improvements found or wanted</h2><p className="feedback-text">{item.improvements}</p>
        <p><a href={`${evidence}/screenshot`}>Download screenshot</a> · <a href={`${evidence}/html`}>Download HTML snapshot</a></p><pre className="feedback-context">{JSON.stringify(item.context, null, 2)}</pre></details>
      <details><summary>Revision history</summary>{item.history.map(entry => <div key={entry.revision}><p>Revision {entry.revision} · {entry.kind}</p><pre className="feedback-context">{JSON.stringify(entry.contents, null, 2)}</pre></div>)}</details>
    </>}
  </section>;
}
