import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Shell } from '../components/layout/Shell';
import { fetchExperimentComparison } from '../api/client';
import type { ExperimentComparison } from '../api/types';
import { Button } from '../components/design/Button';
import { Badge } from '../components/design/Badge';

export const ExperimentPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<ExperimentComparison | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    fetchExperimentComparison(id)
      .then((res: ExperimentComparison) => {
        setData(res);
        setError(null);
      })
      .catch((err: any) => setError(err.message || 'Failed to load experiment comparison'))
      .finally(() => setLoading(false));
  }, [id]);

  return (
    <Shell>
      <div style={{ maxWidth: '1000px' }}>
        <div style={{ marginBottom: '24px', borderBottom: '1px solid var(--dp-color-line)', paddingBottom: '16px' }}>
          <div style={{ fontSize: '11px', color: 'var(--dp-color-accent)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
            BEHAVIORAL YIELD STATISTICAL EVALUATION
          </div>
          <h2 style={{ fontSize: '28px', margin: '4px 0 8px 0', fontFamily: 'var(--dp-typography-display)' }}>
            EXPERIMENT: {id}
          </h2>
          <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>
            Paired Control vs. Treatment Comparison (IEEE Comparative Methodology)
          </div>
        </div>

        {loading ? (
          <div style={{ padding: '30px 0', fontSize: '13px', color: 'var(--dp-color-accent)' }}>
            COMPUTING STATISTICAL YIELD & AGGREGATE DELTAS...
          </div>
        ) : error ? (
          <div style={{ padding: '20px', border: '1px solid var(--dp-color-interference)', background: 'var(--dp-color-plate)' }}>
            <h4 style={{ color: 'var(--dp-color-interference)', margin: '0 0 8px 0' }}>Evaluation Error</h4>
            <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>{error}</div>
          </div>
        ) : data ? (
          <div>
            {/* Horizontal comparison records instead of rounded cards */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
                gap: '16px',
                marginBottom: '24px',
              }}
            >
              {/* CONTROL ARM */}
              <div
                style={{
                  border: '1px solid var(--dp-color-line)',
                  background: 'var(--dp-color-plate)',
                  padding: '18px 20px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                  <span style={{ fontWeight: 600, color: 'var(--dp-color-ink)', fontSize: '14px' }}>
                    CONTROL ARM (BASELINE)
                  </span>
                  <Badge label="CONTROL" variant="neutral" />
                </div>
                <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)', marginBottom: '16px' }}>
                  No active deception primitives applied. Pure telemetry monitoring.
                </div>

                {data.control_session ? (
                  <div>
                    <div style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)', marginBottom: '4px' }}>SESSION ID</div>
                    <code style={{ fontSize: '12px', color: 'var(--dp-color-ink)' }}>{data.control_session}</code>
                    <div style={{ marginTop: '14px' }}>
                      <Link to={`/sessions/${encodeURIComponent(data.control_session)}`} style={{ textDecoration: 'none' }}>
                        <Button size="sm">INSPECT CONTROL RUN</Button>
                      </Link>
                    </div>
                  </div>
                ) : (
                  <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>No control session recorded.</div>
                )}
              </div>

              {/* TREATMENT ARM */}
              <div
                style={{
                  border: '1px solid var(--dp-color-accent)',
                  background: 'var(--dp-color-plate)',
                  padding: '18px 20px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                  <span style={{ fontWeight: 600, color: 'var(--dp-color-accent)', fontSize: '14px' }}>
                    TREATMENT ARM (ADAM DECEPTION)
                  </span>
                  <Badge label="TREATMENT" variant="accent" />
                </div>
                <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)', marginBottom: '16px' }}>
                  Reactive closed-loop environment mutations active upon ATT&CK intent detection.
                </div>

                {data.treatment_session ? (
                  <div>
                    <div style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)', marginBottom: '4px' }}>SESSION ID</div>
                    <code style={{ fontSize: '12px', color: 'var(--dp-color-accent)' }}>{data.treatment_session}</code>
                    <div style={{ marginTop: '14px' }}>
                      <Link to={`/sessions/${encodeURIComponent(data.treatment_session)}`} style={{ textDecoration: 'none' }}>
                        <Button size="sm" variant="primary">INSPECT TREATMENT RUN</Button>
                      </Link>
                    </div>
                  </div>
                ) : (
                  <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>No treatment session recorded.</div>
                )}
              </div>
            </div>

            {/* Raw JSON comparison dump */}
            <div style={{ marginTop: '24px' }}>
              <h3 style={{ fontSize: '13px', color: 'var(--dp-color-ink)', marginBottom: '8px' }}>
                Statistical Yield Report Payload
              </h3>
              <pre
                style={{
                  padding: '14px',
                  background: 'var(--dp-color-plate)',
                  border: '1px solid var(--dp-color-line)',
                  fontSize: '11px',
                  fontFamily: 'var(--dp-typography-mono)',
                  color: 'var(--dp-color-registration)',
                  overflowX: 'auto',
                }}
              >
                {JSON.stringify(data, null, 2)}
              </pre>
            </div>
          </div>
        ) : null}
      </div>
    </Shell>
  );
};
