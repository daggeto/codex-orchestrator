import type { ReactNode } from 'react';
import { Alert, AlertDescription } from './primitives/alert';
export function ResourceState({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <Alert variant={error ? 'destructive' : 'default'} className="resource-state" role={error ? 'alert' : 'status'}><AlertDescription>{children}</AlertDescription></Alert>;
}
