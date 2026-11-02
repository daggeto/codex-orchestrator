import type { ComponentProps } from 'react';
import { Button as Primitive } from './primitives/button';
export function Button({ variant = 'secondary', className = '', type = 'button', ...props }: Omit<ComponentProps<typeof Primitive>, 'variant'> & { variant?: 'primary' | 'secondary' | 'danger' | 'neutral' | 'warning' | 'success' }) {
  return <Primitive type={type} variant={variant === 'primary' ? 'default' : variant === 'danger' ? 'destructive' : 'outline'} className={`button ${variant} ${className}`} {...props} />;
}
