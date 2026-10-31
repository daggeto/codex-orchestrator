import type { HTMLAttributes } from 'react';
// Adapted from Horizon UI's Card; see /third-party-notices.txt.
export function Card({ className = '', ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={`card relative flex flex-col ${className}`} {...props} />;
}
