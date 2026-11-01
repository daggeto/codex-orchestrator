import { useEffect, useState } from 'react';
import { get } from './api';

// Polling persisted cursors repairs missed live observations and reconnects.
export function useResource<T>(path: string | null, interval = 3000) {
  const [state, setState] = useState<{ path: string | null; data?: T; error: string }>({ path, error: '' });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const data = await get<T>(path!, controller.signal);
        if (!controller.signal.aborted) setState({ path, data, error: '' });
      } catch (reason) {
        if (!controller.signal.aborted) setState(previous => ({ path, data: previous.path === path ? previous.data : undefined, error: String(reason) }));
      } finally {
        if (!controller.signal.aborted && interval) timer = setTimeout(refresh, interval);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, interval]);
  return state.path === path ? state : { path, data: undefined, error: '' };
}
