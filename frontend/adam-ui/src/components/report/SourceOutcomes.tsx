import React from 'react';
import type { SourceOutcome, SessionFailure } from '../../api/types';
import { Badge } from '../design/Badge';

interface Props {
  sourceOutcomes?: Record<string, SourceOutcome>;
  failures?: SessionFailure[];
  sessionError?: string | null;
}

export const SourceOutcomes: React.FC<Props> = ({
  sourceOutcomes,
  failures,
  sessionError,
}) => {
  const outcomes = Object.entries(sourceOutcomes || {});

  return (
    <div style={{ marginBottom: '24px' }}>
      {sessionError && (
        <div
          style={{
            padding: '12px 16px',
            background: 'rgba(255, 101, 216, 0.1)',
            border: '1px solid var(--dp-color-interference)',
            color: 'var(--dp-color-interference)',
            marginBottom: '16px',
            fontSize: '12px',
          }}
        >
          <strong>SESSION FAULT:</strong> {sessionError}
        </div>
      )}

      <h3
        style={{
          fontSize: '14px',
          letterSpacing: '0.05em',
          marginBottom: '10px',
          color: 'var(--dp-color-ink)',
        }}
      >
        Capture Source Verification
      </h3>

      {outcomes.length === 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>
          No source outcome telemetry reported.
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
                <th style={{ padding: '8px 12px', color: 'var(--dp-color-ink-muted)' }}>SOURCE</th>
                <th style={{ padding: '8px 12px', color: 'var(--dp-color-ink-muted)' }}>STATUS</th>
                <th style={{ padding: '8px 12px', color: 'var(--dp-color-ink-muted)' }}>RAW EVENTS</th>
                <th style={{ padding: '8px 12px', color: 'var(--dp-color-ink-muted)' }}>LIVE EVENTS</th>
                <th style={{ padding: '8px 12px', color: 'var(--dp-color-ink-muted)' }}>ERRORS / FAULTS</th>
              </tr>
            </thead>
            <tbody>
              {outcomes.map(([key, outcome]) => {
                const srcName = outcome.source || key;
                const status = outcome.status || (outcome.verified ? 'VERIFIED' : 'ACTIVE');
                const isErr = outcome.errors && outcome.errors.length > 0;

                return (
                  <tr
                    key={key}
                    style={{
                      borderBottom: '1px solid var(--dp-color-line)',
                      background: 'var(--dp-color-ground)',
                    }}
                  >
                    <td style={{ padding: '8px 12px', fontWeight: 600 }}>{srcName}</td>
                    <td style={{ padding: '8px 12px' }}>
                      <Badge
                        label={status}
                        variant={status === 'VERIFIED' ? 'accent' : isErr ? 'warn' : 'neutral'}
                      />
                    </td>
                    <td style={{ padding: '8px 12px', fontFamily: 'var(--dp-typography-mono)' }}>
                      {outcome.events_count ?? 0}
                    </td>
                    <td style={{ padding: '8px 12px', fontFamily: 'var(--dp-typography-mono)' }}>
                      {outcome.live_events_count ?? 0}
                    </td>
                    <td style={{ padding: '8px 12px', color: isErr ? 'var(--dp-color-interference)' : 'var(--dp-color-ink-muted)' }}>
                      {isErr ? outcome.errors?.join('; ') : 'None'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {failures && failures.length > 0 && (
        <div style={{ marginTop: '16px' }}>
          <h4 style={{ fontSize: '12px', color: 'var(--dp-color-interference)', marginBottom: '8px' }}>
            Recorded Stage Failures
          </h4>
          <div style={{ border: '1px solid var(--dp-color-line)', background: 'var(--dp-color-plate)' }}>
            {failures.map((f, i) => (
              <div
                key={i}
                style={{
                  padding: '8px 12px',
                  borderBottom: i < failures.length - 1 ? '1px solid var(--dp-color-line)' : 'none',
                  fontSize: '11px',
                }}
              >
                <strong style={{ color: 'var(--dp-color-interference)' }}>[{f.stage} - {f.code}]</strong>{' '}
                <span style={{ color: 'var(--dp-color-ink-body)' }}>{f.description}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
