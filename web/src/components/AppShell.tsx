import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { useLocation } from 'react-router';
import { useAppearance } from '../appearance';
import { RepositorySidebar } from './RepositorySidebar';
import { Button } from './ui/Button';

export function AppShell({ children }: { children: ReactNode }) {
  const { theme, toggleTheme } = useAppearance();
  const location = useLocation();
  const drawer = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { drawer.current?.close(); }, [location.pathname]);
  useEffect(() => {
    const media = matchMedia('(min-width: 1000px)');
    const close = () => { if (media.matches) drawer.current?.close(); };
    media.addEventListener('change', close);
    return () => media.removeEventListener('change', close);
  }, []);
  return <div className="app-shell">
    <a href="#page-content" className="skip-link" onClick={event => { event.preventDefault(); document.getElementById('page-content')?.focus(); }}>Skip to content</a>
    <div className="desktop-sidebar"><RepositorySidebar /></div>
    <dialog ref={drawer} className="navigation-drawer" aria-label="Application navigation" onClose={() => { trigger.current?.focus(); }}>
      <Button onClick={() => drawer.current?.close()}>Close navigation</Button>
      <RepositorySidebar />
    </dialog>
    <div className="app-body">
      <div className="shell-toolbar"><button ref={trigger} type="button" className="mobile-menu" aria-haspopup="dialog" onClick={() => drawer.current?.showModal()}>Open navigation</button>
        <span className="shell-context">Workspace / <strong>{location.pathname === '/monitor' ? 'Service monitor' : 'Orchestrator'}</strong></span>
        <Button onClick={toggleTheme} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>{theme === 'dark' ? '☀ Light mode' : '☾ Dark mode'}</Button>
      </div>
      <main id="page-content" className="repository-content" tabIndex={-1}>{children}</main>
    </div>
  </div>;
}
