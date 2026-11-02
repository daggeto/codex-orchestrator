import { workspaceLabel } from '../workspaceLabel';
import { Link, useParams } from 'react-router';
import { TaskComposer } from '../components/TaskComposer';
import { useNavigation } from '../navigation';
import { Badge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { PageHeader } from '../components/ui/PageHeader';
import { ResourceState } from '../components/ui/ResourceState';

export function RepositoryPage() {
  const { repositoryId } = useParams();
  const { data, error } = useNavigation();
  const repository = data?.repositories.find(item => item.repository_id === repositoryId);
  const main = data?.workspaces.find(item => item.repository_id === repositoryId && item.kind === 'main');
  if (!repository) return <ResourceState error={!!error}>{error || (data ? 'Repository not found' : 'Loading repository…')}</ResourceState>;
  return <><PageHeader eyebrow={<Link to="/">Repositories</Link>} title={repository.name} description={<code>{repository.main_path}</code>} />
    {error && <ResourceState error>{error} · Navigation may be stale.</ResourceState>}
    {main && <TaskComposer key={main.workspace_id} workspace={main} stale={!!error} />}
    <Card><h2>Worktrees</h2><ul className="resource-list">{data?.workspaces.filter(item => item.repository_id === repositoryId).map(item => <li key={item.workspace_id}><Link to={`/workspaces/${item.workspace_id}`}>{workspaceLabel(item)}</Link><Badge>{item.status}</Badge></li>)}</ul></Card>
  </>;
}
