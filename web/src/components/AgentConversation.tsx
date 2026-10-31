import { useEffect, useState } from 'react';
import { get } from '../api';
import type { DurableEvent, OwnedAgent } from '../types';
import { ResourceState } from './ui/ResourceState';
import { MessageContent } from './MessageContent';

export function AgentHistory({ agentId, taskId }: { agentId: string; taskId?: string }) {
  const [events, setEvents] = useState<DurableEvent[]>([]);
  const [failure, setFailure] = useState('');
  useEffect(() => {
    setEvents([]); setFailure('');
    let cursor = 0;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await get<{ events: DurableEvent[]; next_cursor: number }>(`/api/agents/${agentId}/events?after=${cursor}&limit=500${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ''}`, controller.signal);
        if (controller.signal.aborted) return;
        cursor = result.next_cursor;
        setEvents(previous => [...previous, ...result.events]); setFailure('');
        timer = setTimeout(refresh, result.events.length === 500 ? 0 : 2000);
      } catch (reason) {
        if (!controller.signal.aborted) { setFailure(String(reason)); timer = setTimeout(refresh, 3000); }
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [agentId, taskId]);
  const messages: { key: string; kind: string; text: string; event: DurableEvent }[] = [];
  for (const event of events) {
    if (event.kind === 'user') messages.push({ key: String(event.id), kind: 'user', text: String(event.payload.text || ''), event });
    else if (event.kind === 'item/agentMessage/delta') {
      const previous = messages[messages.length - 1];
      const delta = String(event.payload.delta || event.payload.textDelta || '');
      if (previous?.kind === 'assistant' && previous.event.turn_id === event.turn_id) previous.text += delta;
      else messages.push({ key: String(event.id), kind: 'assistant', text: delta, event });
    } else if (event.kind === 'error' || event.kind === 'turn/ended' && event.payload.error) {
      messages.push({ key: String(event.id), kind: 'error', text: String(event.payload.message || event.payload.error), event });
    } else if (!['turn/started','turn/completed','turn/ended'].includes(event.kind)) {
      const item = event.payload.item as { type?: string; text?: string } | undefined;
      if (item?.type === 'agentMessage') {
        if (!messages.some(message => message.kind === 'assistant' && message.event.turn_id === event.turn_id)) messages.push({ key: String(event.id), kind: 'assistant', text: item.text || '', event });
      } else messages.push({ key: String(event.id), kind: 'action', text: event.kind, event });
    }
  }
  return <div className="chat-transcript" aria-label="Persisted conversation">
    {messages.map(message => message.kind === 'action' ? <details key={message.key} className="action-result"><summary>{message.text}</summary><pre>{JSON.stringify(message.event.payload, null, 2)}</pre></details> :
      <article key={message.key} className={`chat-message ${message.kind}`}><strong>{message.kind === 'user' ? 'You' : message.kind === 'assistant' ? 'Agent' : 'Error'}</strong><MessageContent text={message.text} /></article>)}
    {!events.length && <ResourceState>No recorded messages yet.</ResourceState>}
    {failure && <p className="error" role="alert">{failure}</p>}
  </div>;
}
// Compatibility for the old Home view: history is durable and read-only here.
export function AgentConversation({ cwd: _cwd }: { cwd: string }) {
  const [agents, setAgents] = useState<OwnedAgent[]>([]);
  const [selected, setSelected] = useState('');
  useEffect(() => { const controller = new AbortController(); void get<{ agents: OwnedAgent[] }>('/api/agents', controller.signal).then(result => setAgents(result.agents)).catch(() => {}); return () => controller.abort(); }, []);
  return <><label>Agent history<select value={selected} onChange={event => setSelected(event.target.value)}><option value="">Select agent</option>{agents.map(agent => <option key={agent.agent_id} value={agent.agent_id}>{agent.role}</option>)}</select></label>{selected && <AgentHistory key={selected} agentId={selected} />}</>;
}
