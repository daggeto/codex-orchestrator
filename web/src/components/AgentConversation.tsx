import { NativeSelect } from './ui/primitives/native-select';
import { useEffect, useRef, useState } from 'react';
import { reduceConversation } from '../conversation';
import { ActivityGroup } from './ActivityGroup';
import { Button } from './ui/Button';
import { get } from '../api';
import type { Attachment, DurableEvent, OwnedAgent } from '../types';
import { ResourceState } from './ui/ResourceState';
import { AttachmentList } from './AttachmentList';
import { MessageContent } from './MessageContent';

export function AgentHistory({ agentId, taskId, attachments = [], label = 'Agent' }: { agentId: string; taskId?: string; attachments?: Attachment[]; label?: string }) {
  const [events, setEvents] = useState<DurableEvent[]>([]);
  const [failure, setFailure] = useState('');
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [unread, setUnread] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useEffect(() => {
    if (following.current) transcript.current?.scrollTo({ top: transcript.current.scrollHeight });
    else setUnread(true);
  }, [events]);
  useEffect(() => {
    setEvents([]); setFailure(''); setLoading(true); setUnread(false); following.current = true;
    let cursor = 0;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        setUpdating(true);
        const result = await get<{ events: DurableEvent[]; next_cursor: number }>(`/api/agents/${agentId}/events?after=${cursor}&limit=500${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ''}`, controller.signal);
        if (controller.signal.aborted) return;
        cursor = result.next_cursor;
        if (result.events.length) setEvents(previous => { const ids = new Set(previous.map(e => e.id)); return [...previous, ...result.events.filter(e => !ids.has(e.id))]; }); setFailure(''); setLoading(false); setUpdating(false);
        timer = setTimeout(refresh, result.events.length === 500 ? 0 : 2000);
      } catch (reason) {
        if (!controller.signal.aborted) { setFailure(String(reason)); setLoading(false); setUpdating(false); timer = setTimeout(refresh, 3000); }
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [agentId, taskId]);
  const messages = reduceConversation(events);
  return <><div ref={transcript} className="chat-transcript conversation-scroll" aria-label="Persisted conversation" role="region" tabIndex={0} onScroll={event => {
    const node = event.currentTarget;
    following.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
    if (following.current) setUnread(false);
  }}>
    {messages.map(message => message.kind === 'activity' ? <ActivityGroup key={message.key} events={message.events} /> :
      <article key={message.key} className={`chat-message ${message.kind}`}><strong>{message.kind === 'user' ? 'You' : message.kind === 'assistant' ? label : 'Error'}</strong><MessageContent text={message.text} />{taskId && message.attachmentIds?.length ? <AttachmentList taskId={taskId} attachments={attachments.filter(file => message.attachmentIds?.includes(file.attachment_id))} /> : null}{message.kind === 'error' && <details><summary>Failure details</summary><pre>{JSON.stringify(message.events[0].payload, null, 2)}</pre></details>}</article>)}
    {loading && <ResourceState>Loading conversation…</ResourceState>}
    {!loading && !failure && !events.length && <ResourceState>No recorded messages yet.</ResourceState>}
  </div>
    {unread && <Button onClick={() => { following.current = true; transcript.current?.scrollTo({ top: transcript.current.scrollHeight }); setUnread(false); }}>New messages ↓</Button>}
    {failure ? <p className="error" role="alert">Updates failed: {failure}. {events.length ? 'Showing recorded history. Retrying…' : 'Retrying…'}</p> : <small className="conversation-updating">{updating ? 'Updating…' : 'History up to date'}</small>}
  </>;

}
// Compatibility for the old Home view: history is durable and read-only here.
export function AgentConversation({ cwd: _cwd }: { cwd: string }) {
  const [agents, setAgents] = useState<OwnedAgent[]>([]);
  const [selected, setSelected] = useState('');
  useEffect(() => { const controller = new AbortController(); void get<{ agents: OwnedAgent[] }>('/api/agents', controller.signal).then(result => setAgents(result.agents)).catch(() => {}); return () => controller.abort(); }, []);
  return <><label>Agent history<NativeSelect value={selected} onChange={event => setSelected(event.target.value)}><option value="">Select agent</option>{agents.map(agent => <option key={agent.agent_id} value={agent.agent_id}>{agent.role}</option>)}</NativeSelect></label>{selected && <AgentHistory key={selected} agentId={selected} />}</>;
}
