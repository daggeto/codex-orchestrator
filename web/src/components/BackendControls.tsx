import { useEffect, useRef, useState } from 'react';
import { Button } from './ui/Button';

type State = 'running' | 'starting' | 'stopped';
type Action = 'start' | 'stop' | 'restart';
export function BackendControls() {
  const [state, setState] = useState<State | null>(null);
  const [busy, setBusy] = useState<Action | null>(null);
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const pending = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      if (pending.current) return;
      try {
        const response = await fetch('/__backend', { signal: controller.signal });
        if (response.status === 409) return;
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Backend controls unavailable.');
        if (!['running', 'starting', 'stopped'].includes(result.state)) throw new Error('Backend controls require make dev or make preview.');
        if (!controller.signal.aborted && !pending.current) { setState(result.state); setConnectionError(''); }
      } catch (cause) {
        if (!controller.signal.aborted && !pending.current) {
          setState(null);
          setConnectionError(cause instanceof Error ? cause.message : 'Backend controls unavailable.');
        }
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);
  async function act(action: Action) {
    if (pending.current) return;
    pending.current = true;
    setBusy(action); setError('');
    try {
      const response = await fetch('/__backend', { method: 'POST', headers: { 'X-Backend-Action': action } });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Backend action failed.');
      setState(result.state);
      setConnectionError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Backend action failed.');
    } finally { pending.current = false; setBusy(null); }
  }
  return <div className="backend-controls" aria-label="Backend controls" aria-busy={!!busy}>
    <span role="status">Backend: {busy ? `${busy === 'start' ? 'starting' : busy === 'stop' ? 'stopping' : 'restarting'}…` : state || 'unavailable'}</span>
    <Button disabled={!!busy || !state} onClick={() => void act(state === 'stopped' ? 'start' : 'stop')}
      title={state === 'stopped' ? 'Start the backend' : 'Stop the backend and interrupt active tasks'}>
      {state === 'stopped' ? 'Start backend' : 'Stop backend'}
    </Button>
    <Button disabled={!!busy || !state || state === 'stopped'} onClick={() => void act('restart')} title="Restart the backend and interrupt active tasks">Restart</Button>
    {(error || connectionError) && <span className="backend-control-error" role="alert">{error || connectionError}</span>}
  </div>;
}
