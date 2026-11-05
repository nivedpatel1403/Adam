import React from 'react';
import { getTacticBadgeStyle } from '../../lib/mitre';

interface Props {
  attckTags: string[];
}

export const AttckCoverage: React.FC<Props> = ({ attckTags }) => {
  return (
    <div style={{ marginBottom: '24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <h3
          style={{
            fontSize: '14px',
            letterSpacing: '0.05em',
            margin: 0,
            color: 'var(--dp-color-ink)',
          }}
        >
          MITRE ATT&CK® Intent & Technique Coverage
        </h3>
        <span style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)' }}>
          {attckTags.length} techniques correlated
        </span>
      </div>

      {attckTags.length === 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>
          No ATT&CK techniques mapped for this detonation record.
        </div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {attckTags.map((tag, idx) => {
            const { label, style } = getTacticBadgeStyle(tag);
            return (
              <span
                key={idx}
                style={{
                  display: 'inline-block',
                  padding: '3px 8px',
                  fontSize: '11px',
                  fontFamily: 'var(--dp-typography-mono)',
                  background: style.bg,
                  color: style.color,
                  border: `1px solid ${style.border}`,
                  letterSpacing: '0.03em',
                }}
              >
                {label}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
};

