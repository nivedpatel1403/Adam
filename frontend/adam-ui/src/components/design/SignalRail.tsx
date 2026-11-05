import React from 'react';

interface SignalRailProps {
  children: React.ReactNode;
}

export const SignalRail: React.FC<SignalRailProps> = ({ children }) => {
  return (
    <div
      style={{
        display: 'flex',
        minHeight: '100%',
        width: '100%',
        position: 'relative',
      }}
    >
      {/* 2px green accent signal rail */}
      <div
        style={{
          width: '2px',
          background: 'var(--dp-color-accent)',
          flexShrink: 0,
          boxShadow: '0 0 8px rgba(56, 255, 142, 0.4)',
        }}
      />
      <div
        style={{
          flex: 1,
          paddingLeft: 'var(--dp-spacing-gutter)',
          paddingRight: 'var(--dp-spacing-gutter)',
          paddingTop: '24px',
          paddingBottom: '48px',
          overflowX: 'hidden',
        }}
      >
        {children}
      </div>
    </div>
  );
};

