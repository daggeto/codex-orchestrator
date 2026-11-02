import { useEffect, useState } from 'react';
import { ApiError, get } from './api';
interface Resource<T> {
  path: string | null; data?: T; error: string; refreshing: boolean;
  httpStatus?: number; lastSuccess?: number;
}
// Each generation owns an abort controller, including explicit Retry generations.
export function useResource<T>(path: string | null, interval = 3000) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<Resource<T>>({ path, error: '', refreshing: false });
  useEffect(() => {
    if (!path) return;
    const resourcePath = path;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      setState(previous => ({ ...(previous.path === path ? previous : { path, error: '' }), refreshing: true }));
      try {
        const data = await get<T>(resourcePath, controller.signal);
        if (!controller.signal.aborted) setState({ path, data, error: '', refreshing: false, httpStatus: 200, lastSuccess: Date.now() });
      } catch (reason) {
        if (!controller.signal.aborted) setState(previous => ({ ...previous, path, error: String(reason), refreshing: false, httpStatus: reason instanceof ApiError ? reason.status : undefined }));
      } finally {
        if (!controller.signal.aborted && interval) timer = setTimeout(refresh, interval);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, interval, revision]);
  const current: Resource<T> = state.path === path ? state : { path, data: undefined, error: '', refreshing: false };
  return { ...current, loading: !!path && current.data === undefined && !current.error,
    stale: !!current.error, missing: current.httpStatus === 404, fresh: !!current.lastSuccess && !current.error,
    refresh: () => setRevision(value => value + 1) };
}
