import { useEffect, useId, useState } from 'react';
import { get, post } from '../api';
import type { Project } from '../types';

export function ProjectPicker({ value, onChange, disabled }: { value: string; onChange: (value: string) => void; disabled: boolean }) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState('');
  const [searchError, setSearchError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      get<{ projects: Project[] }>(`/api/projects?query=${encodeURIComponent(query)}`, controller.signal)
        .then(result => { if (!controller.signal.aborted) { setProjects(result.projects); setSearchError(''); } })
        .catch(reason => { if (!controller.signal.aborted) setSearchError(String(reason)); });
    }, 180);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [query, revision]);
  const register = async (create: boolean) => {
    setBusy(true); setError('');
    try {
      const result = await post<{ project: Project }>('/api/projects', { path: query, create_if_missing: create });
      onChange(result.project.path); setQuery(''); setRevision(previous => previous + 1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Registration failed'); }
    finally { setBusy(false); }
  };
  const basename = value.split(/[\\/]/).filter(Boolean).pop() || value;
  return <details className="project-picker">
    <summary title={value || 'Choose a project'}>Project: <strong>{basename || 'Choose a project'}</strong>
      {(error || searchError) && <span className="error" role="status"> · Project error</span>}
    </summary>
    <div className="project-panel">
      <label htmlFor={id}>Server project directory</label>
      <input id={id} type="search" value={query} disabled={disabled || busy} placeholder="Search projects or enter /absolute/path"
        onChange={event => setQuery(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter') event.preventDefault(); }} aria-describedby={`${id}-help`} />
      <p className="muted" id={`${id}-help`}>Paths refer to the machine running Orchestrator.</p>
      <div className="project-options" aria-label="Registered projects">
        {projects.map(project => <button type="button" className={`project-option ${value === project.path ? 'selected' : ''}`}
          key={project.path} disabled={disabled || busy || !project.available} aria-pressed={value === project.path} onClick={() => onChange(project.path)}>
          <strong>{project.name}</strong><small>{project.path}{!project.available && ' · unavailable'}</small>
        </button>)}
        {!projects.length && <p className="muted">No matching registered projects.</p>}
      </div>
      <p>Selected: <code>{value || 'Choose a project'}</code></p>
      {query.trim() && <div><button type="button" disabled={disabled || busy} onClick={() => { void register(false); }}>Register existing folder</button>
        <button type="button" disabled={disabled || busy} onClick={() => { void register(true); }}>Create folder and register</button>
        <small>Creating a folder also creates missing parent directories.</small></div>}
      {busy && <p role="status">Registering project…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {searchError && <p className="error" role="alert">{searchError}</p>}
    </div>
  </details>;
}
