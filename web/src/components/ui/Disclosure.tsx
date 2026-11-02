import type { ReactNode } from 'react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './primitives/collapsible';
export function Disclosure({ title, children, defaultOpen = false, className = '' }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean; className?: string }) {
  return <Collapsible defaultOpen={defaultOpen} className={`disclosure ${className}`}>
    <CollapsibleTrigger className="disclosure-trigger">{title}</CollapsibleTrigger>
    <CollapsibleContent keepMounted>{children}</CollapsibleContent>
  </Collapsible>;
}
