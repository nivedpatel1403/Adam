import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LaunchForm, SessionPage } from './pages';
import { WorkspaceContext } from './context';
import type { Sandbox, Summary } from './api';
import { useSessionStream } from './hooks';
import ConsoleApp from './ConsoleApp';

const sandbox: Sandbox = { vm_state: 'Off', agent_healthy: false, current_snapshot: 'clean', vm_name: 'Windows', profile: 'Windows', busy: false, error: null, readiness_timeout_seconds: 420 };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const summary: Summary = {
  metadata: { session_id: 'sess_failed', experiment_id: 'exp_failed', arm: 'CONTROL', status: 'FAILED',
    sample: { filename: 'smoke_sample.exe', sha256: 'a'.repeat(64), md5: 'b'.repeat(32), size_bytes: 2048, file_type: 'PE32' },
    config: { deception_enabled: false, policy_ruleset: 'default', vm_profile: 'Windows', network_mode: 'HOST_ONLY', timeout_seconds: 300 },
    started_at: '2026-11-05T10:00:00Z', ended_at: '2026-11-05T10:07:00Z',
    metrics: { raw_events: 0, semantic_events: 0, decisions_total: 0, decisions_executed: 0, mutations_applied: 0 },
    error: 'Guest agent readiness timed out', failures: [{ stage: 'prepare', code: 'Timeout', description: 'Guest agent did not respond' }],
    source_outcomes: { sysmon: { enabled: true, events_count: 0, live_events_count: 0, errors: ['Source unavailable'] } },
  }, mode: 'live', counts: { events: 101, decisions: 0, mutations: 0 }, attck: [], indicators: [], indicators_truncated: false,
  artifacts: [{ kind: 'raw', filename: 'raw.jsonl', size_bytes: 0, url: '/api/v1/sessions/sess_failed/artifacts/raw' }], comparison_available: false, provisional: false, phase: 'Finalizing',
};

function mockApi(liveStatus = 202) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/samples')) return response({ samples: [{ id: 'smoke', filename: 'smoke_sample.exe', size_bytes: 2048, sha256: 'a'.repeat(64) }] });
    if (url.includes('/sessions/live')) return liveStatus === 202 ? response({ session_id: 'sess_new', status: 'PENDING', sample: 'smoke_sample.exe' }, 202) : response({ detail: 'A VM operation is already active' }, liveStatus);
    if (url.endsWith('/summary')) return response(summary);
    if (url.includes('/telemetry/')) return response({ items: [], total: 101, next_offset: url.includes('offset=100') ? null : 100 });
    return response({});
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
function renderLaunch() {
  return render(<MemoryRouter><WorkspaceContext.Provider value={{ sandbox, sandboxError: null, archive: null, archiveError: null, refresh: vi.fn() }}><Routes><Route path="/" element={<LaunchForm/>}/><Route path="/sessions/:id" element={<h1>Session opened</h1>}/></Routes></WorkspaceContext.Provider></MemoryRouter>);
}

describe('Sandbox status recovery', () => {
  it('retries a failed management query and enables launch only after a valid response', async () => {
    let unavailable = true;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/sandbox/status')) return response({ ...sandbox, error: unavailable ? 'Management query timed out' : null });
      if (url.endsWith('/samples')) return response({ samples: [{ id: 'smoke', filename: 'smoke_sample.exe', size_bytes: 2048, sha256: 'a'.repeat(64) }] });
      return response({ items: [], total: 0, next_offset: null });
    });
    vi.stubGlobal('fetch', fetcher);
    window.history.replaceState({}, '', '/');
    render(<ConsoleApp/>);
    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: 'Launch analysis' })).toBeDisabled();
    unavailable = false;
    await userEvent.click(screen.getByRole('button', { name: 'Retry sandbox status' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Launch analysis' })).toBeEnabled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/sessions/live'))).toBe(false);
  });
});

describe('Real sample launch', () => {
  it('launches the allowlisted binary in control mode and navigates to its returned ID', async () => {
    const api = mockApi(); renderLaunch(); const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Launch analysis' }));
    await screen.findByRole('heading', { name: 'Session opened' });
    const call = api.mock.calls.find(([url]) => String(url).includes('/sessions/live'))!;
    expect(String(call[0])).toContain('sample_id=smoke');
    expect(String(call[0])).toContain('deception_enabled=false');
    expect((call[1]!.body as FormData).has('file')).toBe(false);
  });
  it('submits actual uploaded bytes and explicit treatment selection', async () => {
    const api = mockApi(); renderLaunch(); const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Upload a sample' }));
    const file = new File(['real-sample-bytes'], 'sample.exe', { type: 'application/octet-stream' });
    await user.upload(screen.getByLabelText('Choose an executable or binary'), file);
    await user.selectOptions(screen.getByLabelText('Analysis arm'), 'treatment');
    await user.click(screen.getByRole('button', { name: 'Launch analysis' }));
    await screen.findByText('Session opened');
    const call = api.mock.calls.find(([url]) => String(url).includes('/sessions/live'))!;
    expect(String(call[0])).toContain('deception_enabled=true');
    expect(String(call[0])).not.toContain('sample_id=');
    const sent = (call[1]!.body as FormData).get('file') as File;
    expect(sent.name).toBe('sample.exe'); expect(sent.size).toBe(file.size);
  });
  it('keeps the selection and shows the actual launch failure for explicit retry', async () => {
    const api = mockApi(409); renderLaunch(); const user = userEvent.setup();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Launch analysis' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Launch analysis' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('A VM operation is already active');
    expect(screen.getByLabelText('Sample')).toHaveValue('smoke');
    expect(api.mock.calls.filter(([url]) => String(url).includes('/sessions/live'))).toHaveLength(1);
  });
});

describe('Stored results', () => {
  it('renders startup failure, source errors, partial artifacts, and bounded page navigation', async () => {
    const api = mockApi();
    render(<MemoryRouter initialEntries={['/sessions/sess_failed']}><Routes><Route path="/sessions/:id" element={<SessionPage/>}/></Routes></MemoryRouter>);
    expect(await screen.findByText('Guest agent readiness timed out')).toBeInTheDocument();
    expect(screen.getByText('Source unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Finalizing')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /raw.jsonl/ })).toHaveAttribute('href', '/api/v1/sessions/sess_failed/artifacts/raw');
    expect(screen.queryByRole('link', { name: /sysmon.evtx/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }));
    await waitFor(() => expect(api.mock.calls.some(([url]) => String(url).includes('offset=100&limit=100'))).toBe(true));
    expect(screen.getByText('101–101 of 101')).toBeInTheDocument();
  });
});

class TestStream extends EventTarget {
  static instances: TestStream[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(_url: string) { super(); TestStream.instances.push(this); }
  close() { this.closed = true; }
}
function StreamProbe({ refresh }: { refresh: () => void }) {
  const state = useSessionStream('sess_active', true, refresh);
  return <><span>{state.connection}</span><output>{state.updates.length}</output></>;
}
afterEach(() => { TestStream.instances = []; vi.useRealTimers(); });

it('keeps one stream through rerenders, deduplicates IDs, bounds updates, and reports reconnect', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', TestStream);
  const refresh = vi.fn(); const view = render(<StreamProbe refresh={refresh}/>);
  const stream = TestStream.instances[0];
  act(() => { stream.onopen?.(); for (let i = 0; i < 30; i++) { const event = new MessageEvent('SemanticEvent', { data: JSON.stringify({ session_id: 'sess_active', semantic_id: String(i), intent: 'OBSERVED' }) }); stream.dispatchEvent(event); stream.dispatchEvent(event); } });
  expect(screen.getByRole('status')).toHaveTextContent('20');
  view.rerender(<StreamProbe refresh={() => refresh()}/>);
  expect(TestStream.instances).toHaveLength(1);
  act(() => { stream.onerror?.(); });
  expect(screen.getByText(/Reconnecting/)).toBeInTheDocument();
  act(() => { vi.advanceTimersByTime(1001); }); expect(refresh).toHaveBeenCalled();
  view.unmount(); expect(stream.closed).toBe(true);
});
