import { NativeSelect } from './ui/primitives/native-select';
import { useState } from 'react';
import { Card } from './ui/Card';
import { ResourceState } from './ui/ResourceState';
import { Button } from './ui/Button';
import { post } from '../api';
import { useResource } from '../useResource';
import type { RepositoryRecord } from '../types';

interface Listing { path: string; parent: string | null; roots: string[]; directories: { name: string; path: string }[] }
export function DirectoryPicker({ onAdded }: { onAdded: (repository: RepositoryRecord) => void }) {
  const [path, setPath] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const { data, error, refresh } = useResource<Listing>(`/api/filesystem/directories?path=${encodeURIComponent(path)}`, 0);
  return <Card className="directory-picker" aria-label="Add repository"><h2>Add repository</h2><p>Choose a Git folder on the service machine.</p>
    {!data && !error && <ResourceState>Loading folders…</ResourceState>}
    {data && <><NativeSelect aria-label="Browse root" value={data.roots.find(root => data.path.startsWith(root)) || ''} onChange={event => setPath(event.target.value)}>
      {data.roots.map(root => <option key={root}>{root}</option>)}</NativeSelect>
      <p><code>{data.path}</code></p>
      {data.parent && <Button onClick={() => setPath(data.parent!)}>Parent folder</Button>}
      {!data.directories.length && <p className="muted">No subfolders in this directory.</p>}<div className="directory-list">{data.directories.map(entry => <Button key={entry.path} onClick={() => setPath(entry.path)}>{entry.name}/</Button>)}</div>
      <Button variant="primary" disabled={busy || !!error} onClick={async () => {
        setBusy(true); setFailure('');
        try { const result = await post<{ repository: RepositoryRecord }>('/api/repositories', { path: data.path }); onAdded(result.repository); }
        catch (reason) { setFailure(String(reason)); } finally { setBusy(false); }
      }}>{busy ? 'Adding repository…' : 'Add this repository'}</Button></>}
    {(error || failure) && <p role="alert" className="error">{failure || error} {error && <Button onClick={refresh}>Retry folders</Button>}</p>}
  </Card>;
}
