import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { TaskComposer } from '../components/TaskComposer';
import { useNavigation } from '../navigation';
import { useResource } from '../useResource';
import type { TaskRecord } from '../types';

export function WorkspacePage() {
  const { workspaceId } = useParams();
  const { data, error } = useNavigation();
  const workspace = data?.workspaces.find(item => item.workspace_id === workspaceId);
  const [before, setBefore] = useState('');
  useEffect(() => { setBefore(''); }, [workspaceId]);
  const history = useResource<{ tasks: TaskRecord[]; next_cursor: string | null }>(`/api/workspaces/${workspaceId}/tasks?before=${encodeURIComponent(before)}`);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  if (!workspace) return <p>{error || (data ? 'Workspace not found' : 'Loading workspace…')}</p>;
  return <><h1>{workspace.kind === 'main' ? 'Main' : workspace.branch}</h1><p>{workspace.status} · <code>{workspace.path}</code></p>
    {workspace.status === 'active' && <TaskComposer key={workspace.workspace_id} workspace={workspace} />}
    {workspace.archive && <p>Archive: {workspace.archive.stage} {workspace.archive.path} {workspace.archive.error}</p>}
    {workspace.kind === 'worktree' && !['archived','archiving'].includes(workspace.status) && <button disabled={busy} onClick={async () => {
      setBusy(true); setFailure('');
      try {
        const response = await fetch(`/api/workspaces/${workspace.workspace_id}`, { method: 'DELETE' });
        if (!response.ok) { const result = await response.json(); throw new Error(result.error || 'Archive failed'); }
      } catch (reason) { setFailure(String(reason)); } finally { setBusy(false); }
    }}>{busy ? 'Saving archive…' : 'Archive and delete worktree'}</button>}
    {failure && <p className="error" role="alert">{failure}</p>}
    <h2>Task history</h2><ul>{history.data?.tasks.map(task => <li key={task.task_id}><Link to={`/tasks/${encodeURIComponent(task.task_id)}`}>{task.title}</Link> · {task.status}</li>)}</ul>
    {history.error && <p className="error">{history.error}</p>}
    {before && <button onClick={() => setBefore('')}>Newest tasks</button>}
    {history.data?.tasks.length === 50 && <button onClick={() => setBefore(history.data!.next_cursor || '')}>Older tasks</button>}
  </>;
}
