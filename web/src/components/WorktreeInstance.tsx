import { useState } from 'react';
import { post } from '../api';
import { useResource } from '../useResource';
import { Button } from './ui/Button';
import { ResourceState } from './ui/ResourceState';

interface Instance {
  state: 'stopped' | 'starting' | 'running' | 'failed';
  url?: string;
  data_dir?: string;
  log_path?: string;
}

export function WorktreeInstance({ workspaceId, disabled }: { workspaceId: string; disabled: boolean }) {
  const path = `/api/workspaces/${encodeURIComponent(workspaceId)}/instance`;
  const resource = useResource<Instance>(path);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function act(stop: boolean) {
    setBusy(true);
    setError('');
    try {
      if (stop) {
        const response = await fetch(path, { method: 'DELETE', signal: AbortSignal.timeout(60000) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not stop the test instance.');
      } else {
        await post<Instance>(path, {}, 60000);
      }
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); resource.refresh(); }
  }
  const running = resource.data?.state === 'running';
  const starting = resource.data?.state === 'starting';
  const retained = running || resource.data?.state === 'failed';
  return <div className="workspace-merge">
    <h3>Test this worktree</h3>
    <p className="muted">Runs this worktree’s frontend and API on separate local ports with a fresh database. Live tasks and history are not copied. Stopping retains the temporary data and logs; starting again creates a fresh instance.</p>
    {running && resource.data?.url && <p><a href={resource.data.url} target="_blank" rel="noreferrer">Open test app ↗</a></p>}
    <Button disabled={busy || starting || (!running && disabled) || !resource.data || !!resource.error} onClick={() => void act(running)}>
      {busy || starting ? 'Please wait…' : running ? 'Stop test instance' : 'Start test instance'}
    </Button>
    {retained && !running && <Button disabled={busy} onClick={() => void act(true)}>Stop remaining processes</Button>}
    {resource.data?.state === 'failed' && <ResourceState error>The test instance exited. Check the log before restarting.</ResourceState>}
    {resource.data?.log_path && <p className="muted">Log: <code>{resource.data.log_path}</code><br />Data: <code>{resource.data.data_dir}</code></p>}
    {(error || resource.error) && <ResourceState error>{error || (resource.httpStatus === 404 ? 'Restart the orchestrator backend to enable test instances.' : resource.error)}</ResourceState>}
  </div>;
}
