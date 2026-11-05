import React from 'react';
import type { SemanticEvent, MutationResult } from '../../api/types';
import { Badge } from '../design/Badge';
import { formatLocalTime } from '../../lib/time';

interface TimelineItem {
  time: string;
  type: 'EVENT' | 'MUTATION';
  title: string;
  confidence?: number;
  detector?: string;
  status?: string;
  plausibility?: number;
  changes?: any[];
}

interface Props {
  events: SemanticEvent[];
  mutations: MutationResult[];
}

export const BehavioralTimeline: React.FC<Props> = ({ events, mutations }) => {
  const items: TimelineItem[] = [];

  for (const e of events) {
    items.push({
      time: e.window_start,
      type: 'EVENT',
      title: e.intent,
      confidence: e.confidence,
      detector: e.detector,
    });
  }

  for (const m of mutations) {
    items.push({
      time: m.applied_at,
      type: 'MUTATION',
      title: m.primitive,
      status: m.status,
      plausibility: m.plausibility_score,
      changes: m.changes,
    });
  }

  items.sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());

  // Deduplicate consecutive identical items
  const deduped: TimelineItem[] = [];
  for (const item of items) {
    if (
      deduped.length > 0 &&
      deduped[deduped.length - 1].title === item.title &&
      deduped[deduped.length - 1].time === item.time
    ) {
      continue;
    }
    deduped.push(item);
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3
          style={{
            fontSize: '14px',
            letterSpacing: '0.05em',
            margin: 0,
            color: 'var(--dp-color-ink)',
          }}
        >
          Chronological Behavioral Sequence & Deception Interventions
        </h3>
        <span style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)' }}>
          {deduped.length} temporal entries
        </span>
      </div>

      {deduped.length === 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>
          No chronological activity recorded in this session.
        </div>
      ) : (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            maxHeight: '480px',
            overflowY: 'auto',
            paddingRight: '6px',
          }}
        >
          {deduped.map((item, idx) => {
            const isEvent = item.type === 'EVENT';
            const timeStr = formatLocalTime(item.time, false, true);

            return (
              <div
                key={idx}
                style={{
                  background: 'var(--dp-color-plate)',
                  border: '1px solid var(--dp-color-line)',
                  borderLeft: `3px solid ${isEvent ? 'var(--dp-color-accent)' : '#f59e0b'}`,
                  padding: '10px 14px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                    <Badge
                      label={item.type}
                      variant={isEvent ? 'accent' : 'warn'}
                    />
                    <strong style={{ color: 'var(--dp-color-ink)', fontSize: '13px' }}>
                      {item.title}
                    </strong>

                    {isEvent && item.confidence !== undefined && (
                      <span
                        style={{
                          fontSize: '10px',
                          padding: '1px 6px',
                          border: '1px solid var(--dp-color-control-line)',
                          color:
                            item.confidence >= 0.8
                              ? 'var(--dp-color-accent)'
                              : item.confidence >= 0.5
                              ? '#fbbf24'
                              : 'var(--dp-color-interference)',
                        }}
                      >
                        {Math.round(item.confidence * 100)}% CONF
                      </span>
                    )}

                    {!isEvent && item.status && (
                      <Badge label={item.status} variant="warn" />
                    )}
                    {!isEvent && item.plausibility !== undefined && (
                      <span
                        style={{
                          fontSize: '10px',
                          padding: '1px 6px',
                          border: '1px solid var(--dp-color-control-line)',
                          color: 'var(--dp-color-registration)',
                        }}
                      >
                        PLAUS: {item.plausibility.toFixed(2)}
                      </span>
                    )}
                  </div>

                  <span
                    style={{
                      fontFamily: 'var(--dp-typography-mono)',
                      fontSize: '11px',
                      color: 'var(--dp-color-ink-muted)',
                    }}
                  >
                    {timeStr}
                  </span>
                </div>

                <div style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)' }}>
                  {isEvent ? (
                    <span>
                      Detector Correlation:{' '}
                      <code style={{ color: 'var(--dp-color-registration)' }}>
                        {item.detector || 'FusionDetector'}
                      </code>
                    </span>
                  ) : (
                    <span>
                      Dynamic Deception Mutation applied.{' '}
                      {item.changes && item.changes.length > 0
                        ? `Target: ${item.changes[0]?.target || 'Environmental node'}`
                        : ''}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
