import React from 'react';
import type { AnalysisSession } from '../../api/types';

interface Props {
  session: AnalysisSession;
}

export const ForensicStrip: React.FC<Props> = ({ session }) => {
  const sampleName = session.sample?.filename || 'smoke_sample.exe';
  const sha256 = session.sample?.sha256 || 'N/A';
  const vmProfile = session.config?.vm_profile || 'bare_control';
  const deceptionActive =
    session.config?.deception_enabled ?? (session.arm === 'TREATMENT');

  return (
    <div
      style={{
        padding: '14px 18px',
        border: '1px solid var(--dp-color-line)',
        background: 'var(--dp-color-plate)',
        fontSize: '12px',
        marginBottom: '24px',
      }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px',
          marginBottom: '10px',
        }}
      >
        <div>
          <span style={{ color: 'var(--dp-color-ink-muted)' }}>Target Executable:</span>{' '}
          <strong style={{ color: 'var(--dp-color-ink)', fontFamily: 'var(--dp-typography-mono)' }}>
            {sampleName}
          </strong>
        </div>

        <div>
          <span style={{ color: 'var(--dp-color-ink-muted)' }}>VM Profile:</span>{' '}
          <strong style={{ color: 'var(--dp-color-ink)' }}>{vmProfile}</strong>
        </div>

        <div>
          <span style={{ color: 'var(--dp-color-ink-muted)' }}>Deception State:</span>{' '}
          <strong
            style={{
              color: deceptionActive ? 'var(--dp-color-accent)' : 'var(--dp-color-ink-muted)',
            }}
          >
            {deceptionActive ? 'ACTIVE (TREATMENT)' : 'MONITOR ONLY (CONTROL)'}
          </strong>
        </div>
      </div>

      <div
        style={{
          borderTop: '1px solid var(--dp-color-line)',
          paddingTop: '8px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          flexWrap: 'wrap',
        }}
      >
        <span
          style={{
            color: 'var(--dp-color-ink-muted)',
            fontSize: '11px',
            textTransform: 'uppercase',
          }}
        >
          SHA-256 Hash:
        </span>
        <code
          style={{
            fontFamily: 'var(--dp-typography-mono)',
            fontSize: '11px',
            color: 'var(--dp-color-registration)',
            background: 'var(--dp-color-ground)',
            padding: '2px 8px',
            border: '1px solid var(--dp-color-line)',
            wordBreak: 'break-all',
            userSelect: 'all',
          }}
          title="Click to copy full hash"
        >
          {sha256}
        </code>
      </div>
    </div>
  );
};
