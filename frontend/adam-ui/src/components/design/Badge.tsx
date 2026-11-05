import React from 'react';

interface BadgeProps {
  label: string;
  variant?: 'accent' | 'neutral' | 'warn' | 'interference' | 'registration';
  style?: React.CSSProperties;
}

export const Badge: React.FC<BadgeProps> = ({ label, variant = 'neutral', style }) => {
  const colorMap = {
    accent: {
      bg: 'rgba(56, 255, 142, 0.1)',
      color: 'var(--dp-color-accent)',
      border: 'var(--dp-color-accent)',
    },
    neutral: {
      bg: 'rgba(255, 255, 255, 0.05)',
      color: 'var(--dp-color-ink-muted)',
      border: 'var(--dp-color-line)',
    },
    warn: {
      bg: 'rgba(245, 158, 11, 0.1)',
      color: '#fbbf24',
      border: '#f59e0b',
    },
    interference: {
      bg: 'rgba(255, 101, 216, 0.1)',
      color: 'var(--dp-color-interference)',
      border: 'var(--dp-color-interference)',
    },
    registration: {
      bg: 'rgba(101, 248, 255, 0.1)',
      color: 'var(--dp-color-registration)',
      border: 'var(--dp-color-registration)',
    },
  }[variant];

  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        fontSize: 'var(--dp-typography-metadata)',
        fontFamily: 'var(--dp-typography-mono)',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        background: colorMap.bg,
        color: colorMap.color,
        border: `1px solid ${colorMap.border}`,
        ...style,
      }}
    >
      {label}
    </span>
  );
};

