import React from 'react';

interface MetricCellProps {
  label: string;
  value: string | number;
  subtext?: string;
}

export const MetricCell: React.FC<MetricCellProps> = ({ label, value, subtext }) => {
  return (
    <div
      style={{
        padding: '16px 20px',
        border: '1px solid var(--dp-color-line)',
        background: 'var(--dp-color-plate)',
        position: 'relative',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '3px',
          height: '100%',
          background: 'var(--dp-color-accent)',
        }}
      />
      <div
        style={{
          fontSize: 'var(--dp-typography-metadata)',
          color: 'var(--dp-color-ink-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
          marginBottom: '6px',
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: '28px',
          fontFamily: 'var(--dp-typography-display)',
          fontWeight: 600,
          color: 'var(--dp-color-ink)',
          lineHeight: 1.1,
        }}
      >
        {value}
      </div>
      {subtext && (
        <div
          style={{
            fontSize: '11px',
            color: 'var(--dp-color-ink-muted)',
            marginTop: '4px',
          }}
        >
          {subtext}
        </div>
      )}
    </div>
  );
};

