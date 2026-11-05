import React from 'react';
import { Shell } from '../components/layout/Shell';

export const DashboardPage: React.FC = () => {
  return (
    <Shell>
      <div style={{ maxWidth: '960px' }}>
        <div style={{ marginBottom: '32px' }}>
          <div
            style={{
              fontSize: '12px',
              color: 'var(--dp-color-accent)',
              textTransform: 'uppercase',
              letterSpacing: '0.12em',
              marginBottom: '6px',
            }}
          >
            Real-Time Adaptive Deception Infrastructure
          </div>
          <h1
            style={{
              fontSize: 'clamp(32px, 5vw, 56px)',
              margin: '0 0 16px 0',
              fontFamily: 'var(--dp-typography-display)',
              letterSpacing: '-0.03em',
              lineHeight: 1.1,
            }}
          >
            ADAM SANDBOX CONTROL CONSOLE
          </h1>
          <p
            style={{
              color: 'var(--dp-color-ink-body)',
              fontSize: '14px',
              maxWidth: '680px',
              lineHeight: 1.6,
            }}
          >
            Automated Dynamic Deception and Malware Analysis platform. Select an archived session from
            the telemetry rail to inspect full behavioral yields, MITRE ATT&CK coverage, extracted IOCs,
            and dynamic mutation records, or trigger a live detonation via the left control panel.
          </p>
        </div>

        {/* Overview feature matrix in open ruled cells */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
            gap: '1px',
            background: 'var(--dp-color-line)',
            border: '1px solid var(--dp-color-line)',
            marginBottom: '32px',
          }}
        >
          <div style={{ background: 'var(--dp-color-plate)', padding: '20px' }}>
            <div style={{ fontSize: '11px', color: 'var(--dp-color-accent)', marginBottom: '8px', fontWeight: 600 }}>
              01 // MULTI-SOURCE TELEMETRY FUSION
            </div>
            <div style={{ color: 'var(--dp-color-ink)', fontWeight: 600, marginBottom: '6px' }}>
              Sysmon, Procmon, & Network Ingestion
            </div>
            <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)', lineHeight: 1.5 }}>
              Live ETW event log extraction, registry and file activity monitoring, and packet capture aggregation.
            </div>
          </div>

          <div style={{ background: 'var(--dp-color-plate)', padding: '20px' }}>
            <div style={{ fontSize: '11px', color: 'var(--dp-color-registration)', marginBottom: '8px', fontWeight: 600 }}>
              02 // CLOSED-LOOP MUTATION ENGINE
            </div>
            <div style={{ color: 'var(--dp-color-ink)', fontWeight: 600, marginBottom: '6px' }}>
              Reactive Decoy Injection
            </div>
            <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)', lineHeight: 1.5 }}>
              Planted credential lures, decoy shares, synthetic domain controllers, and process baiting during runtime.
            </div>
          </div>

          <div style={{ background: 'var(--dp-color-plate)', padding: '20px' }}>
            <div style={{ fontSize: '11px', color: 'var(--dp-color-interference)', marginBottom: '8px', fontWeight: 600 }}>
              03 // ATT&CK INTENT ATTRIBUTION
            </div>
            <div style={{ color: 'var(--dp-color-ink)', fontWeight: 600, marginBottom: '6px' }}>
              Behavioral Yield Evaluation
            </div>
            <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)', lineHeight: 1.5 }}>
              Automated comparison between Control and Treatment arms to quantify adversary elicitation and dwell time.
            </div>
          </div>
        </div>

        {/* Operational Directive Box */}
        <div
          style={{
            padding: '16px 20px',
            border: '1px solid var(--dp-color-line)',
            background: 'var(--dp-color-plate)',
            fontSize: '12px',
            display: 'flex',
            alignItems: 'center',
            gap: '16px',
          }}
        >
          <span style={{ fontSize: '20px', color: 'var(--dp-color-accent)' }}>⚡</span>
          <div>
            <div style={{ fontWeight: 600, color: 'var(--dp-color-ink)' }}>Ready for Detonation or Replay</div>
            <div style={{ color: 'var(--dp-color-ink-muted)' }}>
              Use the sidebar to launch a live detonation inside VirtualBox, run an offline synthetic replay, or navigate to <strong>SIMULATE</strong> to run comparative experiments.
            </div>
          </div>
        </div>
      </div>
    </Shell>
  );
};

