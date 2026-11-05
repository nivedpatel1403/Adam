import { useCallback, useEffect, useRef, useState } from 'react';
import { base, request } from './api';

export function useResource<T>(path: string, interval = 0, shouldPoll?: (value: T) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const previousPath = useRef(path);
  const predicate = useRef(shouldPoll);
  useEffect(() => { predicate.current = shouldPoll; }, [shouldPoll]);
  const refresh = useCallback(() => setRevision(v => v + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    if (previousPath.current !== path) {
      setData(null); setLoading(true); setError(null); previousPath.current = path;
    }
    const polling: { timer?: ReturnType<typeof setTimeout> } = {};
    async function load() {
      let repeat = true;
      try {
        const result = await request<T>(path, { signal: controller.signal });
        repeat = predicate.current?.(result) !== false;
        if (!controller.signal.aborted) { setData(result); setError(null); }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to reach the API');
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (interval && repeat) polling.timer = setTimeout(load, interval);
        }
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(polling.timer); };
  }, [path, revision, interval]);
  return { data, error, loading, refresh };
}

export function useSessionStream(sessionId: string, active: boolean, refresh: () => void) {
  const callback = useRef(refresh);
  useEffect(() => { callback.current = refresh; }, [refresh]);
  const [connection, setConnection] = useState('Connecting');
  const [updates, setUpdates] = useState<string[]>([]);
  useEffect(() => {
    if (!active) return;
    const stream = new EventSource(`${base}/api/v1/console/stream?session_id=${encodeURIComponent(sessionId)}`);
    const seen = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    function changed(event: MessageEvent) {
      let payload: Record<string, unknown>;
      try { payload = JSON.parse(event.data); } catch { return; }
      if (payload.session_id && payload.session_id !== sessionId) return;
      const id = String(payload.semantic_id || payload.decision_id || payload.mutation_id || '');
      if (id && seen.has(id)) return;
      if (id) seen.add(id);
      if (seen.size > 1000) seen.delete(seen.values().next().value!);
      if (event.type === 'SemanticEvent') setUpdates(previous => [String(payload.intent || 'Behavior observed'), ...previous].slice(0, 20));
      if (!timer) timer = setTimeout(() => { timer = undefined; callback.current(); }, 1000);
    }
    stream.onopen = () => { setConnection('Connected'); callback.current(); };
    stream.onerror = () => setConnection('Reconnecting · polling active');
    for (const type of ['SemanticEvent', 'PolicyDecision', 'MutationResult', 'SessionLifecycle', 'SessionProgress', 'SessionCompleted', 'ResyncRequired']) {
      stream.addEventListener(type, changed as EventListener);
    }
    return () => { clearTimeout(timer); stream.close(); };
  }, [sessionId, active]);
  return { connection: active ? connection : 'Recorded session', updates };
}
