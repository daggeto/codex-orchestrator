import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import type { OwnedAgent, RepositoryRecord, TaskRecord, WorkspaceRecord } from './types';
import { useLocation } from 'react-router';
import { useResource } from './useResource';

export interface Navigation { repositories: RepositoryRecord[]; workspaces: WorkspaceRecord[]; tasks: TaskRecord[]; agents: OwnedAgent[] }
const NavigationContext = createContext<(ReturnType<typeof useResource<Navigation>> & { expanded: Record<string, boolean>; toggle: (id: string) => void; selected: ReturnType<typeof useResource<import('./types').TaskView>>; selectedId: string | null }) | null>(null);
export function NavigationProvider({ children }: { children: ReactNode }) {
  const { pathname, search } = useLocation();
  const contextId = pathname.startsWith('/tasks/') ? decodeURIComponent(pathname.slice(7)) : pathname.startsWith('/agents/') ? new URLSearchParams(search).get('task_id') : null;
  const selected = useResource<import('./types').TaskView>(contextId ? `/api/tasks/${encodeURIComponent(contextId)}` : null);
  const selectedId = pathname.startsWith('/agents/') && !selected.data?.agents.some(a => pathname === `/agents/${a.agent_id}`) ? null : contextId;
  const resource = useResource<Navigation>('/api/navigation');
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => { try { const saved: unknown = JSON.parse(localStorage.getItem('repository-expansion') || '{}'); return saved && typeof saved === 'object' && !Array.isArray(saved) ? Object.fromEntries(Object.entries(saved).filter(([, value]) => typeof value === 'boolean')) : {}; } catch { return {}; } });
  const toggle = (id: string) => setExpanded(previous => { const next = { ...previous, [id]: previous[id] === false }; try { localStorage.setItem('repository-expansion', JSON.stringify(next)); } catch { /* Storage is optional. */ } return next; });
  return <NavigationContext.Provider value={{ ...resource, expanded, toggle, selected, selectedId }}>{children}</NavigationContext.Provider>;
}
export function useNavigation() {
  const resource = useContext(NavigationContext);
  if (!resource) throw new Error('NavigationProvider is required');
  return resource;
}
