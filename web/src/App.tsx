import { useEffect, useState } from 'react';
import { Home } from './Home';
import { Monitor } from './Monitor';

export function App() {
  const [page, setPage] = useState(() => window.location.hash === '#monitor' ? 'monitor' : 'home');
  const [monitorOpened, setMonitorOpened] = useState(page === 'monitor');
  useEffect(() => {
    const navigate = () => {
      const next = window.location.hash === '#monitor' ? 'monitor' : 'home';
      setPage(next);
      if (next === 'monitor') setMonitorOpened(true);
    };
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);
  return <>
    <nav className="app-nav" aria-label="Application"><a className="brand" href="#home">Orchestrator</a>
      <a href="#home" aria-current={page === 'home' ? 'page' : undefined}>Task home</a>
      <a href="#monitor" aria-current={page === 'monitor' ? 'page' : undefined}>Monitor</a></nav>
    <div hidden={page !== 'home'}><Home /></div>
    {monitorOpened && <div hidden={page !== 'monitor'}><Monitor /></div>}
  </>;
}
