import React from 'react';
import { SandboxStatusWidget } from '../sandbox/SandboxStatusWidget';
import { SandboxControls } from '../sandbox/SandboxControls';
import type { SandboxStatus } from '../../api/types';
import { Link, useLocation } from 'react-router-dom';

interface Props {
  sessions: string[];
  currentSessionId?: string;
  sandboxStatus: SandboxStatus | null;
  loadingStatus: boolean;
  onSelectSession: (id: string) => void;
  onRefreshSessions: () => void;
}

export const Sidebar: React.FC<Props> = ({
  sessions,
  currentSessionId,
  sandboxStatus,
  loadingStatus,
  onSelectSession,
  onRefreshSessions,
}) => {
  const location = useLocation();

  return (
    <aside className="sidebar-container">
      {/* Brand & Platform Identity */}
      <div style={{ padding: '20px 20px 16px', borderBottom: '1px solid var(--dp-color-line)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
          <span style={{ color: 'var(--dp-color-accent)', fontSize: '20px' }}>🛡️</span>
          <h1
            style={{
              fontSize: '22px',
              letterSpacing: '0.04em',
              margin: 0,
              fontFamily: 'var(--dp-typography-display)',
            }}
          >
            ADAM
          </h1>
        </div>
        <div
          style={{
            fontSize: '11px',
            color: 'var(--dp-color-accent)',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}
        >
          Adaptive Deception Sandbox
        </div>
        <div style={{ fontSize: '10px', color: 'var(--dp-color-ink-muted)', marginTop: '2px' }}>
          IEEE Threat Intelligence Instrument
        </div>
      </div>

      {/* Nav links */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          borderBottom: '1px solid var(--dp-color-line)',
        }}
      >
        <Link
          to="/"
          style={{
            padding: '10px',
            textAlign: 'center',
            fontSize: '11px',
            textDecoration: 'none',
            color: location.pathname === '/' ? 'var(--dp-color-accent)' : 'var(--dp-color-ink-muted)',
            borderRight: '1px solid var(--dp-color-line)',
            background: location.pathname === '/' ? 'var(--dp-color-ground)' : 'transparent',
            fontWeight: 600,
          }}
        >
          CONSOLE
        </Link>
        <Link
          to="/simulate"
          style={{
            padding: '10px',
            textAlign: 'center',
            fontSize: '11px',
            textDecoration: 'none',
            color: location.pathname === '/simulate' ? 'var(--dp-color-accent)' : 'var(--dp-color-ink-muted)',
            background: location.pathname === '/simulate' ? 'var(--dp-color-ground)' : 'transparent',
            fontWeight: 600,
          }}
        >
          SIMULATE
        </Link>
      </div>

      {/* Sandbox Live Status */}
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--dp-color-line)' }}>
        <SandboxStatusWidget status={sandboxStatus} loading={loadingStatus} />
      </div>

      {/* VM Execution Controls */}
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--dp-color-line)' }}>
        <SandboxControls
          sandboxStatus={sandboxStatus}
          onSessionLaunched={(id) => {
            onRefreshSessions();
            onSelectSession(id);
          }}
        />
      </div>

      {/* Session Navigation Explorer */}
      <div style={{ padding: '16px 20px', display: 'flex', flexDirection: 'column' }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '10px',
          }}
        >
          <span style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Telemetry Archive ({sessions.length})
          </span>
          <button
            type="button"
            onClick={onRefreshSessions}
            title="Refresh database records"
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--dp-color-ink-muted)',
              fontSize: '12px',
              padding: '2px 6px',
            }}
          >
            ↻
          </button>
        </div>

        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '3px',
            maxHeight: '220px',
            overflowY: 'auto',
            paddingRight: '4px',
            border: '1px solid var(--dp-color-line)',
            background: 'var(--dp-color-ground)',
            padding: '6px',
          }}
        >
          {sessions.length === 0 ? (
            <div style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)' }}>No records located in DB.</div>
          ) : (
            sessions.map((sid) => {
              const isSelected = sid === currentSessionId;
              const isLive = sid.startsWith('sess_');
              const isReplay = sid.startsWith('replay_');
              const isSim = sid.startsWith('sim_');
              const prefix = isLive ? 'LIVE' : isReplay ? 'REPLAY' : isSim ? 'SIM' : 'SES';

              return (
                <button
                  type="button"
                  key={sid}
                  onClick={() => onSelectSession(sid)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 10px',
                    fontSize: '11px',
                    textAlign: 'left',
                    fontFamily: 'var(--dp-typography-mono)',
                    border: `1px solid ${isSelected ? 'var(--dp-color-accent)' : 'var(--dp-color-line)'}`,
                    background: isSelected ? 'rgba(56, 255, 142, 0.08)' : 'var(--dp-color-ground)',
                    color: isSelected ? 'var(--dp-color-ink)' : 'var(--dp-color-ink-body)',
                  }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {sid}
                  </span>
                  <span
                    style={{
                      fontSize: '9px',
                      padding: '1px 4px',
                      color: isLive
                        ? 'var(--dp-color-accent)'
                        : isReplay
                        ? 'var(--dp-color-registration)'
                        : 'var(--dp-color-interference)',
                      border: '1px solid currentColor',
                      marginLeft: '6px',
                    }}
                  >
                    {prefix}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </aside>
  );
};
