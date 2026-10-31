import { HashRouter, Link, Navigate, Route, Routes, useNavigate } from 'react-router';
import { Monitor } from './Monitor';
import { RepositorySidebar, useNavigation } from './components/RepositorySidebar';
import { DirectoryPicker } from './components/DirectoryPicker';
import { RepositoryPage } from './pages/RepositoryPage';
import { WorkspacePage } from './pages/WorkspacePage';
import { TaskPage } from './pages/TaskPage';
import { AgentPage } from './pages/AgentPage';

function Repositories() {
  const navigate = useNavigate();
  const { data, error } = useNavigation();
  return <><h1>Repositories</h1><ul>{data?.repositories.map(repository => <li key={repository.repository_id}><Link to={`/repositories/${repository.repository_id}`}>{repository.name}</Link> · {repository.main_path}</li>)}</ul>
    {error && <p className="error">{error}</p>}
    <DirectoryPicker onAdded={repository => navigate(`/repositories/${repository.repository_id}`)} /></>;
}
export function App() {
  return <HashRouter><div className="repository-layout"><RepositorySidebar /><main className="repository-content"><Routes>
    <Route path="/" element={<Repositories />} /><Route path="/home" element={<Navigate to="/" replace />} />
    <Route path="/repositories/:repositoryId" element={<RepositoryPage />} />
    <Route path="/workspaces/:workspaceId" element={<WorkspacePage />} />
    <Route path="/tasks/:taskId" element={<TaskPage />} />
    <Route path="/agents/:agentId" element={<AgentPage />} />
    <Route path="/monitor" element={<Monitor />} />
    <Route path="*" element={<p>Page not found. <Link to="/">Repositories</Link></p>} />
  </Routes></main></div></HashRouter>;
}
