import { Link, useNavigate } from 'react-router';
import { useNavigation } from '../navigation';
import { DirectoryPicker } from '../components/DirectoryPicker';
import { Badge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { PageHeader } from '../components/ui/PageHeader';
import { ResourceState } from '../components/ui/ResourceState';

export function RepositoriesPage() {
  const navigate = useNavigate();
  const { data, error } = useNavigation();
  return <><PageHeader eyebrow="Your workspace" title="Repositories" description="Choose a repository to start a task or pick up where you left off." />
    {error && <ResourceState error>{error}{data && ' · Navigation may be stale.'}</ResourceState>}
    {!data && !error && <ResourceState>Loading repositories…</ResourceState>}
    {data && !data.repositories.length && <ResourceState>No repositories yet. Add a Git folder below to get started.</ResourceState>}
    <div className="resource-grid">{data?.repositories.map(repository => <Card key={repository.repository_id} className="repository-card">
      <span className="resource-icon" aria-hidden="true">⌘</span><h2><Link to={`/repositories/${repository.repository_id}`}>{repository.name}</Link></h2>
      <p className="resource-path">{repository.main_path}</p><Badge>{data.workspaces.filter(workspace => workspace.repository_id === repository.repository_id).length} workspaces</Badge>
    </Card>)}</div>
    <DirectoryPicker onAdded={repository => navigate(`/repositories/${repository.repository_id}`)} />
  </>;
}
