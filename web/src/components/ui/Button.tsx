import type { ButtonHTMLAttributes } from 'react';
export function Button({ variant = 'secondary', className = '', type = 'button', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' }) {
  return <button type={type} className={`button ${variant} ${className}`} {...props} />;
}
