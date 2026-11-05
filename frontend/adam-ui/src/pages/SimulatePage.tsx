import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Shell } from '../components/layout/Shell';
import { triggerSimulation, triggerExperiment } from '../api/client';
import { Button } from '../components/design/Button';

export const SimulatePage: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [deception, setDeception] = useState<boolean>(true);
  const [mode, setMode] = useState<'single' | 'paired'>('single');
  const [busy, setBusy] = useState<boolean>(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const navigate = useNavigate();

  const handleRun = async () => {
    if (!file) return;
    setBusy(true);

    try {
      if (mode === 'single') {
        setStatusText('DISPATCHING DETERMINISTIC EVENT INGESTION STREAM...');
        const res = await triggerSimulation(file, deception);
        setStatusText(`SESSION GENERATED: ${res.session_id}`);
        setTimeout(() => {
          navigate(`/sessions/${encodeURIComponent(res.session_id)}`);
        }, 800);
      } else {
        setStatusText('DISPATCHING DUAL-ARM (CONTROL + TREATMENT) EXPERIMENT RUN...');
        const res = await triggerExperiment(file);
        setStatusText(`EXPERIMENT INITIATED: ${res.experiment_id}`);
        setTimeout(() => {
          navigate(`/experiments/${encodeURIComponent(res.experiment_id)}`);
        }, 800);
      }
    } catch (err: any) {
      setStatusText(`EXECUTION ERROR: ${err.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell>
      <div style={{ maxWidth: '800px' }}>
        <div style={{ marginBottom: '24px', borderBottom: '1px solid var(--dp-color-line)', paddingBottom: '16px' }}>
          <div style={{ fontSize: '11px', color: 'var(--dp-color-accent)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
            OFFLINE EVENT GENERATION HARNESS
          </div>
          <h2 style={{ fontSize: '28px', margin: '4px 0 8px 0', fontFamily: 'var(--dp-typography-display)' }}>
            SIMULATION & EXPERIMENT DISPATCH
          </h2>
          <div style={{ fontSize: '12px', color: 'var(--dp-color-ink-muted)' }}>
            Deterministic Sysmon process event generation with closed-loop policy evaluation.
          </div>
        </div>

        <div
          style={{
            border: '1px solid var(--dp-color-line)',
            background: 'var(--dp-color-plate)',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
          }}
        >
          {/* Mode Selector */}
          <div>
            <div style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)', textTransform: 'uppercase', marginBottom: '8px' }}>
              Execution Paradigm
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
              <button
                type="button"
                onClick={() => setMode('single')}
                style={{
                  padding: '10px 14px',
                  textAlign: 'left',
                  background: mode === 'single' ? 'rgba(56, 255, 142, 0.1)' : 'var(--dp-color-ground)',
                  border: `1px solid ${mode === 'single' ? 'var(--dp-color-accent)' : 'var(--dp-color-line)'}`,
                  color: mode === 'single' ? 'var(--dp-color-accent)' : 'var(--dp-color-ink)',
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontWeight: 600, fontSize: '12px' }}>SINGLE SIMULATION</div>
                <div style={{ fontSize: '10px', color: 'var(--dp-color-ink-muted)', marginTop: '2px' }}>
                  Execute one session with selectable deception state
                </div>
              </button>

              <button
                type="button"
                onClick={() => setMode('paired')}
                style={{
                  padding: '10px 14px',
                  textAlign: 'left',
                  background: mode === 'paired' ? 'rgba(56, 255, 142, 0.1)' : 'var(--dp-color-ground)',
                  border: `1px solid ${mode === 'paired' ? 'var(--dp-color-accent)' : 'var(--dp-color-line)'}`,
                  color: mode === 'paired' ? 'var(--dp-color-accent)' : 'var(--dp-color-ink)',
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontWeight: 600, fontSize: '12px' }}>PAIRED EXPERIMENT</div>
                <div style={{ fontSize: '10px', color: 'var(--dp-color-ink-muted)', marginTop: '2px' }}>
                  Simulate CONTROL & TREATMENT in parallel for paper yield metrics
                </div>
              </button>
            </div>
          </div>

          {/* Binary Upload */}
          <div>
            <label
              htmlFor="sim-file-input"
              style={{ display: 'block', fontSize: '11px', color: 'var(--dp-color-ink-muted)', textTransform: 'uppercase', marginBottom: '6px' }}
            >
              Binary Sample File
            </label>
            <input
              id="sim-file-input"
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              style={{
                width: '100%',
                padding: '8px',
                fontSize: '12px',
                background: 'var(--dp-color-ground)',
                border: '1px solid var(--dp-color-line)',
                color: 'var(--dp-color-ink)',
              }}
            />
            {file && (
              <div style={{ fontSize: '11px', color: 'var(--dp-color-registration)', marginTop: '4px' }}>
                Selected: {file.name} ({Math.round(file.size / 1024)} KB)
              </div>
            )}
          </div>

          {/* Deception Toggle for Single Mode */}
          {mode === 'single' && (
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '12px',
                cursor: 'pointer',
                color: 'var(--dp-color-ink)',
              }}
            >
              <input
                type="checkbox"
                checked={deception}
                onChange={(e) => setDeception(e.target.checked)}
                style={{ accentColor: 'var(--dp-color-accent)' }}
              />
              <span>Enable Active Deception (Treatment Arm)</span>
            </label>
          )}

          {/* Submit */}
          <div>
            <Button
              variant="primary"
              disabled={!file || busy}
              onClick={handleRun}
            >
              {busy ? 'SYNTHESIZING RUN...' : mode === 'single' ? 'LAUNCH SIMULATION' : 'LAUNCH COMPARATIVE EXPERIMENT'}
            </Button>
          </div>

          {/* Feedback */}
          {statusText && (
            <div
              style={{
                fontSize: '12px',
                padding: '10px 14px',
                background: 'var(--dp-color-ground)',
                border: '1px solid var(--dp-color-accent)',
                color: 'var(--dp-color-accent)',
                fontFamily: 'var(--dp-typography-mono)',
              }}
            >
              {statusText}
            </div>
          )}
        </div>
      </div>
    </Shell>
  );
};
