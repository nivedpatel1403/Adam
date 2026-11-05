import React from 'react';
import { MetricCell } from '../design/MetricCell';

interface Props {
  eventCount: number;
  mutationCount: number;
  decisionCount: number;
  attckCount: number;
}

export const MetricsRow: React.FC<Props> = ({
  eventCount,
  mutationCount,
  decisionCount,
  attckCount,
}) => {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: '12px',
        marginBottom: '24px',
      }}
    >
      <MetricCell label="Telemetry Events" value={eventCount} subtext="Sysmon / Procmon / Network" />
      <MetricCell label="Deception Mutations" value={mutationCount} subtext="Planted decoys & lures" />
      <MetricCell label="Policy Decisions" value={decisionCount} subtext="Evaluated rules" />
      <MetricCell label="ATT&CK Techniques" value={attckCount} subtext="Adversary behaviors" />
    </div>
  );
};

