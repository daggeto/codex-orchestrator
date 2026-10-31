import { Link, useParams } from 'react-router';
import { AgentHistory } from '../components/AgentConversation';
import { Badge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { PageHeader } from '../components/ui/PageHeader';
import { ResourceState } from '../components/ui/ResourceState';
import { useResource } from '../useResource';
import type { OwnedAgent } from '../types';
export function AgentPage() {
  const { agentId } = useParams();
  const { data, error } = useResource<{ agent: OwnedAgent }>(`/api/agents/${agentId}/events?limit=1`);
  return <><PageHeader title={data?.agent.role || 'Agent'} eyebrow={data?.agent.task_id && <Link to={`/tasks/${encodeURIComponent(data.agent.task_id)}`}>Task and conversation controls</Link>}
    description={agentId} actions={data && <Badge>{data.agent.status}{data.agent.retired_at && ' · Retired'}</Badge>} />
    {!data && !error && <ResourceState>Loading agent…</ResourceState>}
    {error && <ResourceState error>{error}</ResourceState>}
    <Card><h2>Conversation history</h2>{agentId && <AgentHistory key={agentId} agentId={agentId} />}</Card></>;
}
