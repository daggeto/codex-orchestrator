import type { HTMLAttributes } from 'react';
export function Badge({ children, className = '', variant, ...props }: HTMLAttributes<HTMLSpanElement> & { variant?: 'neutral' | 'warning' | 'danger' | 'success' }) {
  return <span className={`badge ${variant ? `badge-${variant}` : ''} ${className}`} {...props}>{children}</span>;
}
