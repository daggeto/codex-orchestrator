import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import type { OwnedAgent, RepositoryRecord, TaskRecord, WorkspaceRecord } from './types';
import { useResource } from './useResource';

export interface Navigation { repositories: RepositoryRecord[]; workspaces: WorkspaceRecord[]; tasks: TaskRecord[]; agents: OwnedAgent[] }
const NavigationContext = createContext<(ReturnType<typeof useResource<Navigation>> & { expanded: Record<string, boolean>; toggle: (id: string) => void }) | null>(null);
export function NavigationProvider({ children }: { children: ReactNode }) {
  const resource = useResource<Navigation>('/api/navigation');
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => { try { return JSON.parse(localStorage.getItem('repository-expansion') || '{}'); } catch { return {}; } });
  const toggle = (id: string) => setExpanded(previous => { const next = { ...previous, [id]: previous[id] === false }; try { localStorage.setItem('repository-expansion', JSON.stringify(next)); } catch { /* Storage is optional. */ } return next; });
  return <NavigationContext.Provider value={{ ...resource, expanded, toggle }}>{children}</NavigationContext.Provider>;
}
export function useNavigation() {
  const resource = useContext(NavigationContext);
  if (!resource) throw new Error('NavigationProvider is required');
  return resource;
}
