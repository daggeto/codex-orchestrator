import type { ReactNode } from 'react';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from './primitives/empty';
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return <Empty><EmptyHeader><EmptyTitle>{title}</EmptyTitle>{children && <EmptyDescription>{children}</EmptyDescription>}</EmptyHeader></Empty>;
}
