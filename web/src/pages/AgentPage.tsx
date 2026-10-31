import { Link, useParams } from 'react-router';
import { AgentHistory } from '../components/AgentConversation';
import { useResource } from '../useResource';
import type { OwnedAgent } from '../types';
export function AgentPage() {
  const { agentId } = useParams();
  const { data, error } = useResource<{ agent: OwnedAgent }>(`/api/agents/${agentId}/events?limit=1`);
  return <><h1>{data?.agent.role || 'Agent'}</h1>{data?.agent.task_id && <Link to={`/tasks/${encodeURIComponent(data.agent.task_id)}`}>Task and conversation controls</Link>}
    <p>{data?.agent.status}{data?.agent.retired_at && ' · Retired'}</p>
    {error && <p role="alert" className="error">{error}</p>}
    {agentId && <AgentHistory key={agentId} agentId={agentId} />}</>;
}
