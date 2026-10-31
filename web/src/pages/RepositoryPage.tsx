import { Link, useParams } from 'react-router';
import { TaskComposer } from '../components/TaskComposer';
import { useNavigation } from '../navigation';

export function RepositoryPage() {
  const { repositoryId } = useParams();
  const { data, error } = useNavigation();
  const repository = data?.repositories.find(item => item.repository_id === repositoryId);
  const main = data?.workspaces.find(item => item.repository_id === repositoryId && item.kind === 'main');
  if (!repository) return <p>{error || (data ? 'Repository not found' : 'Loading repository…')}</p>;
  return <><h1>{repository.name}</h1><p><code>{repository.main_path}</code></p>
    {main && <TaskComposer key={main.workspace_id} workspace={main} />}
    <h2>Workspaces</h2><ul>{data?.workspaces.filter(item => item.repository_id === repositoryId).map(item => <li key={item.workspace_id}><Link to={`/workspaces/${item.workspace_id}`}>{item.kind === 'main' ? 'Main' : item.branch}</Link> · {item.status}</li>)}</ul>
  </>;
}
