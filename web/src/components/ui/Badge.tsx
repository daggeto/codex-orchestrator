import type { HTMLAttributes } from 'react';
export function Badge({ children, className = '', ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={`badge ${className}`} {...props}>{children}</span>;
}
