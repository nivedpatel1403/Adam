import React, { useState } from 'react';
import type { IOCItem } from '../../lib/ioc';
import { Badge } from '../design/Badge';

interface Props {
  iocs: IOCItem[];
}

export const IOCTable: React.FC<Props> = ({ iocs }) => {
  const [filter, setFilter] = useState<'ALL' | 'EVENT' | 'MUTATION'>('ALL');

  const filtered = iocs.filter((item) => (filter === 'ALL' ? true : item.source === filter));

  return (
    <div style={{ marginBottom: '24px' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '8px',
          marginBottom: '10px',
        }}
      >
        <h3
          style={{
            fontSize: '14px',
            letterSpacing: '0.05em',
            margin: 0,
            color: 'var(--dp-color-ink)',
          }}
        >
          Extracted Indicators & Planted Deception Artifacts
        </h3>

        <div style={{ display: 'flex', gap: '4px' }}>
          {(['ALL', 'EVENT', 'MUTATION'] as const).map((mode) => (
            <button
              type="button"
              key={mode}
              onClick={() => setFilter(mode)}
              style={{
                padding: '3px 8px',
                fontSize: '10px',
                fontFamily: 'var(--dp-typography-mono)',
                background: filter === mode ? 'var(--dp-color-accent)' : 'transparent',
                color: filter === mode ? 'var(--dp-color-ground)' : 'var(--dp-color-ink-muted)',
                border: '1px solid var(--dp-color-line)',
                fontWeight: 600,
              }}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>
          No IOCs or planted decoys match selection.
        </div>
      ) : (
        <div style={{ overflowX: 'auto', border: '1px solid var(--dp-color-line)' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: '12px',
              textAlign: 'left',
            }}
          >
            <thead>
              <tr style={{ background: 'var(--dp-color-plate)', borderBottom: '1px solid var(--dp-color-line)' }}>
                <th style={{ padding: '8px 12px', width: '120px', color: 'var(--dp-color-ink-muted)' }}>
                  ORIGIN
                </th>
                <th style={{ padding: '8px 12px', color: 'var(--dp-color-ink-muted)' }}>
                  TARGET PATH / ARTIFACT / ENDPOINT
                </th>
                <th style={{ padding: '8px 12px', width: '180px', color: 'var(--dp-color-ink-muted)' }}>
                  OPERATION
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((ioc, idx) => {
                const isMut = ioc.source === 'MUTATION';
                return (
                  <tr
                    key={idx}
                    style={{
                      borderBottom: '1px solid var(--dp-color-line)',
                      background: 'var(--dp-color-ground)',
                      borderLeft: `3px solid ${isMut ? '#f59e0b' : 'var(--dp-color-accent)'}`,
                    }}
                  >
                    <td style={{ padding: '8px 12px' }}>
                      <Badge
                        label={ioc.source}
                        variant={isMut ? 'warn' : 'accent'}
                      />
                    </td>
                    <td
                      style={{
                        padding: '8px 12px',
                        fontFamily: 'var(--dp-typography-mono)',
                        color: 'var(--dp-color-ink)',
                        wordBreak: 'break-all',
                      }}
                    >
                      {ioc.target}
                    </td>
                    <td style={{ padding: '8px 12px', color: 'var(--dp-color-ink-muted)' }}>
                      {ioc.operation}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
