import type { ComponentProps } from 'react';
import { Card as Primitive } from './primitives/card';
export function Card({ className = '', ...props }: ComponentProps<typeof Primitive>) {
  return <Primitive className={`card ${className}`} {...props} />;
}
