import { useState, useEffect } from 'react';
import { fetchSandboxStatus } from '../api/client';
import type { SandboxStatus } from '../api/types';

export function useSandboxStatus(pollIntervalMs = 8000) {
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const check = async () => {
    try {
      const res = await fetchSandboxStatus();
      setStatus(res);
    } catch {
      setStatus({
        vm_state: 'unknown',
        agent_healthy: false,
        current_snapshot: 'unknown',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    check();
    const timer = setInterval(check, pollIntervalMs);
    return () => clearInterval(timer);
  }, [pollIntervalMs]);

  return { status, loading, refresh: check };
}
