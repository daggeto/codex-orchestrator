import type { ReactNode } from 'react';
export function ResourceState({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`resource-state ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>;
}
