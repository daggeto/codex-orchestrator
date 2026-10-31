import { createContext, useContext } from 'react';
import type { ReactNode } from 'react';
import type { OwnedAgent, RepositoryRecord, TaskRecord, WorkspaceRecord } from './types';
import { useResource } from './useResource';

export interface Navigation { repositories: RepositoryRecord[]; workspaces: WorkspaceRecord[]; tasks: TaskRecord[]; agents: OwnedAgent[] }
const NavigationContext = createContext<ReturnType<typeof useResource<Navigation>> | null>(null);
export function NavigationProvider({ children }: { children: ReactNode }) {
  const resource = useResource<Navigation>('/api/navigation');
  return <NavigationContext.Provider value={resource}>{children}</NavigationContext.Provider>;
}
export function useNavigation() {
  const resource = useContext(NavigationContext);
  if (!resource) throw new Error('NavigationProvider is required');
  return resource;
}
