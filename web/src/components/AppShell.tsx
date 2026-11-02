import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useLocation } from 'react-router';
import { useAppearance } from '../appearance';
import { useIsMobile } from '../hooks/use-mobile';
import { RepositorySidebar } from './RepositorySidebar';
import { Button } from './ui/Button';
import { Sidebar, SidebarProvider } from './ui/primitives/sidebar';
import { Sheet, SheetClose, SheetContent, SheetTitle, SheetTrigger } from './ui/primitives/sheet';

export function AppShell({ children }: { children: ReactNode }) {
  const { theme, toggleTheme } = useAppearance();
  const location = useLocation();
  const mobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const destination = useRef(false);
  useEffect(() => { setOpen(false); }, [location.pathname, location.search, mobile]);
  return <SidebarProvider className="app-shell" open>
    <a href="#page-content" className="skip-link" onClick={event => { event.preventDefault(); document.getElementById('page-content')?.focus(); }}>Skip to content</a>
    <Sidebar collapsible="none" className="desktop-sidebar"><RepositorySidebar /></Sidebar>
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent side="left" className="sheet-navigation" showCloseButton={false} initialFocus={close} finalFocus={() => destination.current ? document.getElementById('page-content') : trigger.current}
        onClick={event => { if (event.target instanceof Element && event.target.closest('a')) { destination.current = true; setOpen(false); } }}>
        <SheetTitle className="sr-only">Application navigation</SheetTitle>
        <SheetClose ref={close} render={<Button />}>Close navigation</SheetClose>
        <RepositorySidebar />
      </SheetContent>
      <div className="app-body">
        <div className="shell-toolbar"><SheetTrigger ref={trigger} render={<Button className="mobile-menu" />} onClick={() => { destination.current = false; }}>Open navigation</SheetTrigger>
          <span className="shell-context">Workspace / <strong>{location.pathname === '/monitor' ? 'Service monitor' : 'Orchestrator'}</strong></span>
          <Button onClick={toggleTheme} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</Button>
        </div>
        <main id="page-content" className="repository-content" tabIndex={-1}>{children}</main>
      </div>
    </Sheet>
  </SidebarProvider>;
}
