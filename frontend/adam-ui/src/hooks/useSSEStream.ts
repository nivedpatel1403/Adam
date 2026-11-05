import { useEffect } from 'react';

export function useSSEStream(
  onEvent?: (eventType: string, payload: any) => void
) {
  useEffect(() => {
    const apiBase = import.meta.env.VITE_API_BASE_URL || '';
    const es = new EventSource(`${apiBase}/api/v1/stream`);

    const handleMessage = (e: MessageEvent, type: string) => {
      try {
        const parsed = JSON.parse(e.data);
        if (onEvent) {
          onEvent(type, parsed);
        }
      } catch (err) {
        console.error('SSE JSON parse error:', err);
      }
    };

    es.addEventListener('SemanticEvent', (e) => handleMessage(e, 'SemanticEvent'));
    es.addEventListener('PolicyDecision', (e) => handleMessage(e, 'PolicyDecision'));
    es.addEventListener('MutationResult', (e) => handleMessage(e, 'MutationResult'));
    es.addEventListener('SessionCompleted', (e) => handleMessage(e, 'SessionCompleted'));

    return () => {
      es.close();
    };
  }, [onEvent]);
}

