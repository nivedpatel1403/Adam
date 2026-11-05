import React, { useState } from 'react';
import { Button } from '../design/Button';
import { resetSandbox, triggerLiveDetonation, triggerReplay } from '../../api/client';
import type { SandboxStatus } from '../../api/types';

interface Props {
  sandboxStatus: SandboxStatus | null;
  onSessionLaunched: (sessionId: string) => void;
}

export const SandboxControls: React.FC<Props> = ({ sandboxStatus, onSessionLaunched }) => {
  const [sampleSource, setSampleSource] = useState<'benchmark' | 'upload'>('benchmark');
  const [file, setFile] = useState<File | null>(null);
  const [profile, setProfile] = useState<string>('bare_control');
  const [deception, setDeception] = useState<boolean>(true);
  const [busy, setBusy] = useState<boolean>(false);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);

  const vmState = sandboxStatus?.vm_state || 'Off';
  const isHealthy = sandboxStatus?.agent_healthy || false;
  const snapshot = sandboxStatus?.current_snapshot || '';

  const isReady =
    (vmState === 'Off' && (snapshot === 'clean' || snapshot.toLowerCase().includes('golden'))) ||
    (vmState === 'Running' && isHealthy);

  const canDetonate = isReady && !busy && (sampleSource === 'benchmark' || !!file);

  const handleDetonate = async () => {
    setBusy(true);
    setMsg({ text: 'TRANSMITTING PAYLOAD TO HYPERVISOR...' });
    try {
      let targetFile = file;
      if (sampleSource === 'benchmark' || !targetFile) {
        // Create an empty dummy file if benchmark selected
        targetFile = new File(['SMOKE_SAMPLE_EXECUTION_MARKER'], 'smoke_sample.exe', {
          type: 'application/octet-stream',
        });
      }
      const res = await triggerLiveDetonation(targetFile, profile, deception);
      setMsg({ text: `DETONATION LAUNCHED: ${res.sample}` });
    } catch (err: any) {
      setMsg({ text: `DETONATION ERROR: ${err.message}`, error: true });
    } finally {
      setBusy(false);
    }
  };

  const handleReplay = async () => {
    setBusy(true);
    setMsg({ text: 'EXECUTING SYNTHETIC FUSION REPLAY...' });
    try {
      const res = await triggerReplay();
      setMsg({ text: `REPLAY COMPLETE: ${res.session_id}` });
      onSessionLaunched(res.session_id);
    } catch (err: any) {
      setMsg({ text: `REPLAY ERROR: ${err.message}`, error: true });
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async () => {
    setBusy(true);
    setMsg({ text: 'DISCARDING VM DELTAS & RESTORING CLEAN SNAPSHOT...' });
    try {
      await resetSandbox();
      setMsg({ text: 'VM SNAPSHOT RESTORED SUCCESSFULLY' });
    } catch (err: any) {
      setMsg({ text: `RESET FAILED: ${err.message}`, error: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      {/* Sample Selector */}
      <div>
        <div style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)', textTransform: 'uppercase', marginBottom: '6px' }}>
          Payload Source
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px', marginBottom: '6px' }}>
          <button
            type="button"
            onClick={() => setSampleSource('benchmark')}
            style={{
              padding: '6px',
              fontSize: '11px',
              background: sampleSource === 'benchmark' ? 'var(--dp-color-accent)' : 'transparent',
              color: sampleSource === 'benchmark' ? 'var(--dp-color-ground)' : 'var(--dp-color-ink-body)',
              border: '1px solid var(--dp-color-control-line)',
              fontWeight: 600,
            }}
          >
            BENCHMARK
          </button>
          <button
            type="button"
            onClick={() => setSampleSource('upload')}
            style={{
              padding: '6px',
              fontSize: '11px',
              background: sampleSource === 'upload' ? 'var(--dp-color-accent)' : 'transparent',
              color: sampleSource === 'upload' ? 'var(--dp-color-ground)' : 'var(--dp-color-ink-body)',
              border: '1px solid var(--dp-color-control-line)',
              fontWeight: 600,
            }}
          >
            UPLOAD
          </button>
        </div>

        {sampleSource === 'upload' ? (
          <input
            type="file"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            style={{
              width: '100%',
              fontSize: '11px',
              padding: '4px',
              color: 'var(--dp-color-ink-muted)',
            }}
          />
        ) : (
          <div style={{ fontSize: '11px', color: 'var(--dp-color-ink-muted)', padding: '2px 0' }}>
            smoke_sample.exe (Pre-loaded)
          </div>
        )}
      </div>

      {/* VM Profile Selector */}
      <div>
        <label
          htmlFor="profile-select"
          style={{ display: 'block', fontSize: '11px', color: 'var(--dp-color-ink-muted)', textTransform: 'uppercase', marginBottom: '4px' }}
        >
          VM Environment Profile
        </label>
        <select
          id="profile-select"
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
          style={{
            width: '100%',
            padding: '6px 8px',
            fontSize: '12px',
            background: 'var(--dp-color-plate)',
            color: 'var(--dp-color-ink)',
            border: '1px solid var(--dp-color-control-line)',
          }}
        >
          <option value="bare_control">bare_control</option>
          <option value="enterprise_office_decoy">enterprise_office_decoy</option>
          <option value="developer_decoy">developer_decoy</option>
        </select>
      </div>

      {/* Deception Toggle */}
      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          fontSize: '11px',
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
        <span>ENABLE DYNAMIC DECEPTION (TREATMENT)</span>
      </label>

      {/* Action Buttons */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '4px' }}>
        <Button
          variant="primary"
          fullWidth
          disabled={!canDetonate}
          onClick={handleDetonate}
        >
          {busy ? 'RUNNING DETONATION...' : '⚡ RUN LIVE DETONATION'}
        </Button>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={handleReplay}
          >
            🚀 REPLAY
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={handleReset}
          >
            🔄 RESET VM
          </Button>
        </div>
      </div>

      {/* Feedback Messages */}
      {msg && (
        <div
          style={{
            fontSize: '11px',
            padding: '6px 8px',
            border: `1px solid ${msg.error ? 'var(--dp-color-interference)' : 'var(--dp-color-accent)'}`,
            background: 'var(--dp-color-plate)',
            color: msg.error ? 'var(--dp-color-interference)' : 'var(--dp-color-accent)',
            fontFamily: 'var(--dp-typography-mono)',
          }}
        >
          {msg.text}
        </div>
      )}
    </div>
  );
};
