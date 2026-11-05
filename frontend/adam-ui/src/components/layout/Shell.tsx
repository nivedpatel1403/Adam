import React from 'react';
import { Sidebar } from './Sidebar';
import { SignalRail } from '../design/SignalRail';
import { useSessions } from '../../hooks/useSessions';
import { useSandboxStatus } from '../../hooks/useSandboxStatus';
import { useNavigate } from 'react-router-dom';

interface ShellProps {
  currentSessionId?: string;
  children: React.ReactNode;
}

export const Shell: React.FC<ShellProps> = ({ currentSessionId, children }) => {
  const { sessions, refresh: refreshSessions } = useSessions();
  const { status: sandboxStatus, loading: loadingStatus } = useSandboxStatus();
  const navigate = useNavigate();

  return (
    <div className="shell-container">
      <Sidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        sandboxStatus={sandboxStatus}
        loadingStatus={loadingStatus}
        onSelectSession={(sid) => navigate(`/sessions/${encodeURIComponent(sid)}`)}
        onRefreshSessions={refreshSessions}
      />
      <main className="main-content">
        <SignalRail>{children}</SignalRail>
      </main>
    </div>
  );
};
