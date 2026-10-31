import { useEffect, useRef, useState } from 'react';
import { get, post } from '../api';
import type { Agent, ChatMessage } from '../types';
import { Chat } from './Chat';

function Conversation({ agent, cwd }: { agent: Agent; cwd: string }) {
  const key = `orchestrator.conversation.v1:${agent.agent_id}:${cwd}`;
  const [thread, setThread] = useState<string | null>(() => {
    try { return sessionStorage.getItem(key); } catch { return null; }
  });
  const threadRef = useRef(thread);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  return <>
    <p className="muted">{agent.role} · <code>{cwd}</code>{thread && <> · thread <code>{thread}</code></>}. Responses arrive when the turn finishes.</p>
    <Chat label="Message agent" messages={messages} disabled={agent.status !== 'running'} onSubmit={async text => {
      if (!threadRef.current) {
        const created = await post<{ thread_id: string; cwd: string }>(`/api/agents/${encodeURIComponent(agent.agent_id)}/threads`, { cwd });
        threadRef.current = created.thread_id; setThread(created.thread_id);
        try { sessionStorage.setItem(key, created.thread_id); } catch { /* Identity remains available for this mounted conversation. */ }
      }
      setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'user', content: text }]);
      try {
        const result = await post<{ response: string }>(`/api/agents/${encodeURIComponent(agent.agent_id)}/message`,
          { message: text, thread_id: threadRef.current, cwd }, 43200000);
        setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'assistant', content: result.response }]);
      } catch (reason) {
        throw new Error(`${reason instanceof Error ? reason.message : 'Response unavailable'}. The turn may still be running; inspect the monitor before sending again.`);
      }
    }} />
  </>;
}
export function AgentConversation({ cwd }: { cwd: string }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    get<{ agents: Agent[] }>('/api/agents', controller.signal)
      .then(result => { if (!controller.signal.aborted) { setAgents(result.agents); setError(''); } })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [revision]);
  const agent = agents.find(item => item.agent_id === selected);
  return <section className="agent-conversation">
    <div className="agent-refresh"><button type="button" onClick={() => setRevision(value => value + 1)}>Refresh agents</button></div>
    <p className="muted">Direct conversations use running agents and a separate thread for this project.</p>
    <label htmlFor="conversation-agent">Agent</label>
    <select id="conversation-agent" value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">Choose a running agent</option>
      {agents.map(item => <option key={item.agent_id} value={item.agent_id} disabled={item.status !== 'running'}>{item.role} · {item.agent_id} · {item.status}</option>)}
    </select>
    {error && <p className="error" role="alert">{error}</p>}
    {agent && <Conversation key={`${cwd}:${agent.agent_id}`} agent={agent} cwd={cwd} />}
  </section>;
}
