import type { AnalysisSession } from '../../api/types';
import { Badge } from '../design/Badge';
import { formatLocalTime } from '../../lib/time';

interface Props {
  session: AnalysisSession;
}

export const SessionHeader: React.FC<Props> = ({ session }) => {
  const arm = session.arm || 'CONTROL';
  const status = session.status || 'PENDING';
  const netMode = session.config?.network_mode || 'HOST_ONLY';

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        gap: '16px',
        paddingBottom: '20px',
        borderBottom: '1px solid var(--dp-color-line)',
        marginBottom: '24px',
      }}
    >
      <div>
        <div style={{ fontSize: '11px', color: 'var(--dp-color-accent)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '4px' }}>
          SESSION TELEMETRY RECORD
        </div>
        <h2
          style={{
            fontSize: 'clamp(24px, 3vw, 36px)',
            margin: 0,
            fontFamily: 'var(--dp-typography-display)',
            color: 'var(--dp-color-ink)',
            wordBreak: 'break-all',
          }}
        >
          {session.session_id}
        </h2>
        <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)', marginTop: '4px' }}>
          Experiment:{' '}
          <span style={{ color: 'var(--dp-color-registration)', fontFamily: 'var(--dp-typography-mono)' }}>
            {session.experiment_id}
          </span>{' '}
          • Initiated: {formatLocalTime(session.started_at)}
        </div>
      </div>

      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
        <Badge label={`NET: ${netMode}`} variant="registration" />
        <Badge
          label={`ARM: ${arm}`}
          variant={arm === 'TREATMENT' ? 'accent' : 'neutral'}
        />
        <Badge
          label={status}
          variant={
            status === 'COMPLETED'
              ? 'accent'
              : status === 'RUNNING'
              ? 'warn'
              : 'interference'
          }
        />
      </div>
    </div>
  );
};

