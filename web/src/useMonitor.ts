import { useEffect, useState } from 'react';
import { appendEvent, connectionState, get, parseEvent, snapshot } from './api';
import type { MonitorEvent, Selection, Snapshot } from './types';

const empty: Snapshot = { agents: [], tasks: [], runs: [], workspaces: [] };
export function useMonitor() {
  const [data, setData] = useState(empty);
  const [selection, select] = useState<Selection>(null);
  const [events, setEvents] = useState<MonitorEvent[]>([]);
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [streamOpen, setStreamOpen] = useState(false);
  const [stale, setStale] = useState(true);
  const [error, setError] = useState('');
  const [gap, setGap] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    let busy = false;
    let pending = false;
    let debounce: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const refresh = async () => {
      if (disposed) return;
      if (busy) { pending = true; return; }
      busy = true;
      try {
        await get('/health', controller.signal);
        if (!disposed) setReachable(true);
        const next = await snapshot(controller.signal);
        if (!disposed) {
          setData(next); setStale(false); setError(''); setUpdatedAt(new Date().toLocaleTimeString());
        }
      } catch (err) {
        if (!disposed) {
          setStale(true); setError(err instanceof Error ? err.message : 'API request failed');
          // A snapshot endpoint can fail while the instance remains reachable.
          try { await get('/health', controller.signal); if (!disposed) setReachable(true); }
          catch { if (!disposed) setReachable(false); }
        }
      } finally {
        busy = false;
        if (pending && !disposed) { pending = false; void refresh(); }
      }
    };
    const stream = new EventSource('/api/events');
    stream.onopen = () => { setStreamOpen(true); void refresh(); };
    stream.onerror = () => { setStreamOpen(false); setGap(true); setStale(true); void refresh(); };
    stream.onmessage = event => {
      const parsed = parseEvent(event.data);
      if (!parsed) return;
      setEvents(previous => appendEvent(previous, parsed));
      if (/^(agent\.|work\.|workflow\.)/.test(parsed.topic) && !debounce) {
        debounce = setTimeout(() => { debounce = undefined; void refresh(); }, 250);
      }
    };
    void refresh();
    const interval = setInterval(() => { void refresh(); }, 2000);
    return () => {
      disposed = true; controller.abort(); stream.close(); clearInterval(interval); clearTimeout(debounce);
    };
  }, []);
  return { data, selection, select, events, connection: connectionState(reachable, streamOpen), stale, error, gap, updatedAt };
}
