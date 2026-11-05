import { useState, useEffect } from 'react';
import { fetchSessions } from '../api/client';

export function useSessions() {
  const [sessions, setSessions] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setLoading(true);
      const list = await fetchSessions();
      setSessions(list);
      setError(null);
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch sessions');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  return { sessions, loading, error, refresh };
}

