import React from 'react';
import type { SandboxStatus as SandboxStatusType } from '../../api/types';
import { Badge } from '../design/Badge';

interface Props {
  status: SandboxStatusType | null;
  loading: boolean;
}

export const SandboxStatusWidget: React.FC<Props> = ({ status, loading }) => {
  if (loading && !status) {
    return (
      <div style={{ padding: '12px', border: '1px solid var(--dp-color-line)', fontSize: '11px' }}>
        QUERYING SANDBOX METRICS...
      </div>
    );
  }

  const vmState = status?.vm_state || 'Unknown';
  const isHealthy = status?.agent_healthy || false;
  const snapshot = status?.current_snapshot || 'Unknown';

  const isReady =
    (vmState === 'Off' && (snapshot === 'clean' || snapshot.toLowerCase().includes('golden'))) ||
    (vmState === 'Running' && isHealthy);

  return (
    <div
      style={{
        padding: '12px 14px',
        border: '1px solid var(--dp-color-line)',
        background: 'var(--dp-color-plate)',
        fontSize: '11px',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '8px',
        }}
      >
        <span style={{ color: 'var(--dp-color-ink-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Hypervisor Signal
        </span>
        <Badge
          label={isReady ? 'READY' : 'OFFLINE'}
          variant={isReady ? 'accent' : 'interference'}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px', color: 'var(--dp-color-ink-body)' }}>
        <span style={{ color: 'var(--dp-color-ink-muted)' }}>VM:</span>
        <span style={{ fontFamily: 'var(--dp-typography-mono)' }}>{vmState}</span>

        <span style={{ color: 'var(--dp-color-ink-muted)' }}>Agent:</span>
        <span style={{ color: isHealthy ? 'var(--dp-color-accent)' : 'var(--dp-color-interference)' }}>
          {isHealthy ? 'ONLINE (HTTP)' : 'UNREACHABLE'}
        </span>

        <span style={{ color: 'var(--dp-color-ink-muted)' }}>Snap:</span>
        <span style={{ fontFamily: 'var(--dp-typography-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {snapshot}
        </span>
      </div>
    </div>
  );
};
