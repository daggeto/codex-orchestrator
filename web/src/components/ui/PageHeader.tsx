import type { ReactNode } from 'react';
export function PageHeader({ title, description, eyebrow, actions }: { title: string; description?: ReactNode; eyebrow?: ReactNode; actions?: ReactNode }) {
  return <header className="page-header"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description && <div className="page-description">{description}</div>}</div>{actions && <div className="page-actions">{actions}</div>}</header>;
}
