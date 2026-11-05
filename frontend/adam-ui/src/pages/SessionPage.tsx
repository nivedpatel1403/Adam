import React, { useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Shell } from '../components/layout/Shell';
import { useSession } from '../hooks/useSession';
import { useSSEStream } from '../hooks/useSSEStream';
import { SessionHeader } from '../components/report/SessionHeader';
import { MetricsRow } from '../components/report/MetricsRow';
import { DownloadBar } from '../components/report/DownloadBar';
import { ForensicStrip } from '../components/report/ForensicStrip';
import { SourceOutcomes } from '../components/report/SourceOutcomes';
import { AttckCoverage } from '../components/report/AttckCoverage';
import { IOCTable } from '../components/report/IOCTable';
import { BehavioralTimeline } from '../components/report/BehavioralTimeline';
import { extractIOCs } from '../lib/ioc';
import { Button } from '../components/design/Button';
import type { SessionDetailResponse } from '../api/types';

export const SessionPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const { data, loading, error, refresh, setData } = useSession(id);

  // Live SSE stream handler for active detonations
  useSSEStream((type: string, payload: any) => {
    if (!id || !data) return;
    if (payload?.session_id === id) {
      if (type === 'SemanticEvent') {
        setData((prev: SessionDetailResponse | null) =>
          prev ? { ...prev, events: [...prev.events, payload] } : prev
        );
      } else if (type === 'PolicyDecision') {
        setData((prev: SessionDetailResponse | null) =>
          prev ? { ...prev, decisions: [...prev.decisions, payload] } : prev
        );
      } else if (type === 'MutationResult') {
        setData((prev: SessionDetailResponse | null) =>
          prev ? { ...prev, mutations: [...prev.mutations, payload] } : prev
        );
      } else if (type === 'SessionCompleted') {
        refresh();
      }
    }
  });

  const events = data?.events || [];
  const decisions = data?.decisions || [];
  const mutations = data?.mutations || [];
  const session = data?.metadata;

  // Extract unique ATT&CK coverage tags
  const attckTags = useMemo(() => {
    const set = new Set<string>();
    for (const e of events) {
      if (e.attck?.tactic && e.attck?.technique) {
        set.add(`${e.attck.tactic} / ${e.attck.technique}`);
      }
    }
    return Array.from(set);
  }, [events]);

  // Extract IOCs
  const iocs = useMemo(() => {
    return extractIOCs(events, mutations);
  }, [events, mutations]);

  if (loading && !data) {
    return (
      <Shell currentSessionId={id}>
        <div style={{ padding: '40px 0', fontSize: '13px', color: 'var(--dp-color-accent)' }}>
          READING FORENSIC ARTIFACTS AND TELEMETRY STREAM FOR [{id}]...
        </div>
      </Shell>
    );
  }

  if (error || !session) {
    return (
      <Shell currentSessionId={id}>
        <div
          style={{
            padding: '24px',
            border: '1px solid var(--dp-color-interference)',
            background: 'var(--dp-color-plate)',
            maxWidth: '600px',
          }}
        >
          <h3 style={{ color: 'var(--dp-color-interference)', margin: '0 0 12px 0' }}>
            Session Acquisition Failure
          </h3>
          <p style={{ color: 'var(--dp-color-ink-muted)', fontSize: '13px', marginBottom: '16px' }}>
            {error || `Unable to locate session record [${id}] in repository.`}
          </p>
          <Button onClick={refresh}>RETRY FETCH</Button>
        </div>
      </Shell>
    );
  }

  return (
    <Shell currentSessionId={id}>
      <div style={{ maxWidth: '1100px' }}>
        {/* Top Header & Metadata */}
        <SessionHeader session={session} />

        {/* Experiment comparison banner if part of an experiment */}
        {session.experiment_id && (
          <div
            style={{
              padding: '10px 16px',
              border: '1px solid var(--dp-color-line)',
              background: 'var(--dp-color-plate)',
              marginBottom: '20px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>
              Linked in Comparative Experiment: <strong style={{ color: 'var(--dp-color-registration)' }}>{session.experiment_id}</strong>
            </span>
            <Link
              to={`/experiments/${encodeURIComponent(session.experiment_id)}`}
              style={{ textDecoration: 'none' }}
            >
              <Button size="sm">VIEW COMPARISON A/B</Button>
            </Link>
          </div>
        )}

        {/* Numeric Metrics */}
        <MetricsRow
          eventCount={events.length}
          mutationCount={mutations.length}
          decisionCount={decisions.length}
          attckCount={attckTags.length}
        />

        {/* Download Actions Bar */}
        <DownloadBar sessionId={session.session_id} />

        {/* Forensic Metadata Strip */}
        <ForensicStrip session={session} />

        {/* Capture Source Outcomes */}
        <SourceOutcomes
          sourceOutcomes={session.source_outcomes}
          failures={session.failures}
          sessionError={session.error}
        />

        {/* MITRE ATT&CK Coverage */}
        <AttckCoverage attckTags={attckTags} />

        {/* IOCs and Decoys Table */}
        <IOCTable iocs={iocs} />

        {/* Behavioral Sequence Timeline */}
        <BehavioralTimeline events={events} mutations={mutations} />
      </div>
    </Shell>
  );
};
