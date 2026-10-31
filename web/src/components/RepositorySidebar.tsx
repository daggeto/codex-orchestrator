import { Link } from 'react-router';
import type { OwnedAgent, RepositoryRecord, TaskRecord, WorkspaceRecord } from '../types';
import { useResource } from '../useResource';

export interface Navigation { repositories: RepositoryRecord[]; workspaces: WorkspaceRecord[]; tasks: TaskRecord[]; agents: OwnedAgent[] }
export function useNavigation() { return useResource<Navigation>('/api/navigation'); }
export function RepositorySidebar() {
  const { data, error } = useNavigation();
  return <aside className="repository-sidebar"><Link className="brand" to="/">Orchestrator</Link><nav aria-label="Repositories">
    <Link to="/">Add repository</Link><Link to="/monitor">Service monitor</Link>
    {data?.repositories.map(repository => <details key={repository.repository_id} open>
      <summary><Link to={`/repositories/${repository.repository_id}`}>{repository.name}</Link></summary>
      {data.workspaces.filter(workspace => workspace.repository_id === repository.repository_id).map(workspace => <details key={workspace.workspace_id} open>
        <summary><Link to={`/workspaces/${workspace.workspace_id}`}>{workspace.kind === 'main' ? 'Main' : workspace.branch || workspace.path.split('/').pop()} {workspace.status !== 'active' && `(${workspace.status})`}</Link></summary>
        {data.tasks.filter(task => task.workspace_id === workspace.workspace_id).map(task => <details key={task.task_id}>
          <summary><Link to={`/tasks/${encodeURIComponent(task.task_id)}`}>{task.title}</Link></summary>
          {data.agents.filter(agent => agent.task_id === task.task_id).map(agent => <Link key={agent.agent_id} to={`/agents/${agent.agent_id}`}>{agent.role} · {agent.status}</Link>)}
        </details>)}
      </details>)}
    </details>)}
    {error && <p role="alert" className="error">{error}</p>}
  </nav></aside>;
}
