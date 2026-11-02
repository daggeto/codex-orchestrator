import type { ComponentProps } from 'react';
import { Badge as Primitive } from './primitives/badge';
export function Badge({ className = '', variant, ...props }: Omit<ComponentProps<typeof Primitive>, 'variant'> & { variant?: 'neutral' | 'warning' | 'danger' | 'success' }) {
  return <Primitive variant={variant === 'danger' ? 'destructive' : 'outline'} className={`badge ${variant ? `badge-${variant}` : ''} ${className}`} {...props} />;
}
