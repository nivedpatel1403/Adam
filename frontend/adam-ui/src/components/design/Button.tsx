import React from 'react';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  fullWidth?: boolean;
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = 'secondary',
  size = 'md',
  fullWidth = false,
  style,
  disabled,
  ...props
}) => {
  const isPrimary = variant === 'primary';
  const isDanger = variant === 'danger';

  const baseStyle: React.CSSProperties = {
    fontFamily: 'var(--dp-typography-mono)',
    fontSize: size === 'sm' ? '11px' : size === 'lg' ? '14px' : '12px',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    padding: size === 'sm' ? '6px 12px' : size === 'lg' ? '12px 24px' : '8px 16px',
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.45 : 1,
    width: fullWidth ? '100%' : 'auto',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '8px',
    border: '1px solid transparent',
    outline: 'none',
    position: 'relative',
    ...(isPrimary
      ? {
          background: 'var(--dp-color-accent)',
          color: 'var(--dp-color-ground)',
          borderColor: 'var(--dp-color-accent)',
          clipPath: 'polygon(8px 0%, 100% 0%, calc(100% - 8px) 100%, 0% 100%)',
        }
      : isDanger
      ? {
          background: 'rgba(255, 101, 216, 0.1)',
          color: 'var(--dp-color-interference)',
          borderColor: 'var(--dp-color-interference)',
        }
      : variant === 'ghost'
      ? {
          background: 'transparent',
          color: 'var(--dp-color-ink-body)',
          borderColor: 'transparent',
        }
      : {
          background: 'var(--dp-color-plate)',
          color: 'var(--dp-color-ink)',
          borderColor: 'var(--dp-color-control-line)',
        }),
    ...style,
  };

  return (
    <button
      disabled={disabled}
      style={baseStyle}
      {...props}
      onMouseEnter={(e) => {
        if (disabled) return;
        if (isPrimary) {
          e.currentTarget.style.background = '#ffffff';
          e.currentTarget.style.color = '#000000';
        } else {
          e.currentTarget.style.borderColor = 'var(--dp-color-accent)';
          e.currentTarget.style.color = 'var(--dp-color-accent)';
        }
      }}
      onMouseLeave={(e) => {
        if (disabled) return;
        if (isPrimary) {
          e.currentTarget.style.background = 'var(--dp-color-accent)';
          e.currentTarget.style.color = 'var(--dp-color-ground)';
        } else if (isDanger) {
          e.currentTarget.style.borderColor = 'var(--dp-color-interference)';
          e.currentTarget.style.color = 'var(--dp-color-interference)';
        } else {
          e.currentTarget.style.borderColor =
            variant === 'ghost' ? 'transparent' : 'var(--dp-color-control-line)';
          e.currentTarget.style.color =
            variant === 'ghost' ? 'var(--dp-color-ink-body)' : 'var(--dp-color-ink)';
        }
      }}
      onMouseDown={(e) => {
        if (disabled) return;
        e.currentTarget.style.transform = 'translate(1px, 1px)';
      }}
      onMouseUp={(e) => {
        if (disabled) return;
        e.currentTarget.style.transform = 'translate(0px, 0px)';
      }}
    >
      {children}
    </button>
  );
};

