import { useState, useEffect } from 'react';
import { fetchSessionDetail } from '../api/client';
import type { SessionDetailResponse } from '../api/types';

export function useSession(sessionId?: string) {
  const [data, setData] = useState<SessionDetailResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!sessionId) {
      setData(null);
      return;
    }
    try {
      setLoading(true);
      const detail = await fetchSessionDetail(sessionId);
      setData(detail);
      setError(null);
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch session detail');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [sessionId]);

  return { data, loading, error, refresh: load, setData };
}
