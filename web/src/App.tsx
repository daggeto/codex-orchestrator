import { HashRouter, Link, Navigate, Route, Routes } from 'react-router';
import { FeedbackListPage, FeedbackPage } from './pages/FeedbackPage';
import { Monitor } from './Monitor';
import { NavigationProvider } from './navigation';
import { AppShell } from './components/AppShell';
import { RepositoriesPage } from './pages/RepositoriesPage';
import { RepositoryPage } from './pages/RepositoryPage';
import { WorkspacePage } from './pages/WorkspacePage';
import { TasksPage } from './pages/TasksPage';
import { TaskPage } from './pages/TaskPage';
import { AgentPage } from './pages/AgentPage';

export function App() {
  return <HashRouter><NavigationProvider><AppShell><Routes>
    <Route path="/" element={<RepositoriesPage />} /><Route path="/home" element={<Navigate to="/" replace />} />
    <Route path="/repositories/:repositoryId" element={<RepositoryPage />} />
    <Route path="/workspaces/:workspaceId" element={<WorkspacePage />} />
    <Route path="/tasks" element={<TasksPage />} />
    <Route path="/tasks/:taskId" element={<TaskPage />} />
    <Route path="/agents/:agentId" element={<AgentPage />} />
    <Route path="/feedback" element={<FeedbackListPage />} />
    <Route path="/feedback/:feedbackId" element={<FeedbackPage />} />
    <Route path="/monitor" element={<Monitor />} />
    <Route path="*" element={<p>Page not found. <Link to="/">Repositories</Link></p>} />
  </Routes></AppShell></NavigationProvider></HashRouter>;
}
