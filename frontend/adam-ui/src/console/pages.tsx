import { useCallback, useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, ArrowUpRight, CheckCircle2, ChevronLeft, ChevronRight, Download, File, FlaskConical, FolderOpen, Play, Radio, RefreshCw, Search, ShieldCheck, Upload } from 'lucide-react';
import type { AnalysisSession } from '../api/types';
import type { ArchiveItem, Page, RecordItem, Sample, Summary } from './api';
import { base, duration, modeLabel, number, request, shortId, terminal, time } from './api';
import { useResource, useSessionStream } from './hooks';
import { useWorkspace } from './context';
import { Button, CopyValue, Dialog, Empty, Loading, Notice, Panel, Status } from './ui';

function Heading({ eyebrow, title, children, action }: { eyebrow?: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>{children && <p className="page-description">{children}</p>}</div>{action}</div>;
}

export function SessionTable({ items }: { items: ArchiveItem[] }) {
  return <div className="table-scroll" tabIndex={0} role="region" aria-label="Analysis sessions"><table><thead><tr><th>Session / sample</th><th>Origin</th><th>Arm</th><th>Status</th><th>Started</th><th><span className="sr-only">Open session</span></th></tr></thead><tbody>{items.map(({ metadata: s, mode }) => <tr key={s.session_id}><td><Link className="table-primary" to={`/sessions/${s.session_id}`}>{s.sample.filename}</Link><span className="table-secondary mono">{shortId(s.session_id)}</span></td><td>{modeLabel(mode)}</td><td>{s.arm === 'CONTROL' ? 'Control' : 'Treatment'}</td><td><Status value={s.status}/></td><td className="mono nowrap">{time(s.started_at)}</td><td><Link className="icon-link" to={`/sessions/${s.session_id}`} aria-label={`View ${s.sample.filename}, session ${shortId(s.session_id)}`}><ArrowUpRight size={17}/></Link></td></tr>)}</tbody></table></div>;
}

export function LaunchForm() {
  const { sandbox, sandboxError, refresh } = useWorkspace();
  const samples = useResource<{ samples: Sample[] }>('/samples');
  const navigate = useNavigate();
  const [source, setSource] = useState('builtin');
  const [sampleId, setSampleId] = useState('smoke');
  const [file, setFile] = useState<File | null>(null);
  const [arm, setArm] = useState('control');
  const [timeout, setTimeoutSeconds] = useState(300);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = samples.data?.samples.find(sample => sample.id === sampleId);
  const available = source === 'upload' ? !!file : !!selected;
  const blocked = busy || !sandbox || !!sandboxError || !!sandbox.error || sandbox.busy || !available;
  async function launch(event: FormEvent) {
    event.preventDefault();
    if (blocked) return;
    setBusy(true); setError(null);
    const body = new FormData();
    if (source === 'upload' && file) body.append('file', file);
    const query = new URLSearchParams({ deception_enabled: String(arm === 'treatment'), timeout_seconds: String(timeout) });
    if (source === 'builtin') query.set('sample_id', sampleId);
    try {
      const result = await request<{ session_id: string }>(`/sessions/live?${query}`, { method: 'POST', body });
      refresh(); navigate(`/sessions/${result.session_id}`);
    } catch (err) { setError(err instanceof Error ? err.message : 'Analysis could not be started'); }
    finally { setBusy(false); }
  }
  return <Panel title="Launch analysis" action={<span className="quiet-label"><ShieldCheck size={14}/>Host-only execution</span>}>
    <form onSubmit={launch}>
      <div className="segmented" role="group" aria-label="Sample source"><button type="button" aria-pressed={source === 'builtin'} onClick={() => setSource('builtin')}>Built-in sample</button><button type="button" aria-pressed={source === 'upload'} onClick={() => setSource('upload')}>Upload a sample</button></div>
      {source === 'builtin' ? <div className="sample-picker"><File size={23} aria-hidden="true"/><div><label htmlFor="sample-choice">Sample</label><select id="sample-choice" value={sampleId} onChange={e => setSampleId(e.target.value)}>{samples.data?.samples.map(sample => <option value={sample.id} key={sample.id}>{sample.filename} · {number(sample.size_bytes)} bytes</option>)}{!samples.data?.samples.length && <option value="">No built-in sample available</option>}</select>{selected && <span className="sample-hash mono" title={selected.sha256}>SHA256 {selected.sha256.slice(0, 24)}…</span>}</div><span className="sample-origin">Local sample</span></div> : <div className="sample-picker upload-picker"><Upload size={23} aria-hidden="true"/><div><label htmlFor="sample-upload">Choose an executable or binary</label><input id="sample-upload" type="file" accept=".exe,.dll,.bin" aria-describedby="sample-upload-help" onChange={e => setFile(e.target.files?.[0] || null)}/><span id="sample-upload-help" className="small subdued">Maximum 64 MiB. Execution takes place inside the sandbox.</span></div></div>}
      {samples.error && source === 'builtin' && <Notice error>{samples.error}</Notice>}
      <div className="form-grid"><div className="field"><label htmlFor="vm-profile">Sandbox</label><select id="vm-profile" disabled><option>{sandbox?.profile || 'Loading configuration…'}</option></select></div><div className="field"><label htmlFor="analysis-arm">Analysis arm</label><select id="analysis-arm" value={arm} onChange={e => setArm(e.target.value)}><option value="control">Control · observe only</option><option value="treatment">Treatment · adaptive deception</option></select></div><div className="field"><label htmlFor="workload-timeout">Workload timeout</label><select id="workload-timeout" value={timeout} onChange={e => setTimeoutSeconds(Number(e.target.value))}><option value={90}>90 seconds</option><option value={300}>5 minutes</option><option value={600}>10 minutes</option><option value={900}>15 minutes</option></select></div></div>
      <div className="launch-bottom"><p className="small subdued">{sandbox?.busy ? 'Another sandbox operation is active. Please wait.' : 'Restores the clean baseline and starts the guest automatically.'}{sandbox?.readiness_timeout_seconds ? ` Guest readiness has a separate ${Math.round(sandbox.readiness_timeout_seconds / 60)}-minute limit.` : ''}</p><Button primary type="submit" disabled={blocked}><Play size={15} fill="currentColor" aria-hidden="true"/>{busy ? 'Starting…' : 'Launch analysis'}</Button></div>
      {sandboxError && <Notice error>Connect the API before launching an analysis.</Notice>}{!sandbox && !sandboxError && <Notice>Checking sandbox status…</Notice>}
      {error && <Notice error>{error} Your sample and settings have been kept. You can retry the launch.</Notice>}
    </form>
  </Panel>;
}

export function ConsolePage() {
  const { archive, archiveError, sandbox, refresh } = useWorkspace();
  return <><Heading eyebrow="ANALYSIS WORKSPACE" title="Console">Analyze behavior. Inspect the evidence.<br/>A controlled workspace for adaptive malware analysis.</Heading>
    <LaunchForm/>
    <div className="section-heading"><h2>Recent sessions</h2><Link className="text-link" to="/sessions">View all <ArrowRight size={15}/></Link></div>
    <Panel>{archiveError ? <Notice error action={<Button compact onClick={refresh}>Retry</Button>}>{archiveError}</Notice> : !archive ? <Loading text="Loading recent analyses…"/> : archive.items.length ? <SessionTable items={archive.items.slice(0, 5)}/> : <Empty title="Your first analysis starts here">Launch the built-in sample or upload a file to create a session.</Empty>}</Panel>
    <div className="console-notes"><div><Radio size={19} aria-hidden="true"/><h3>Instrumented capture</h3><p>Sysmon, Procmon, and network evidence accompany each live analysis.</p></div><div><ShieldCheck size={19} aria-hidden="true"/><h3>Controlled environment</h3><p>{sandbox?.current_snapshot && sandbox.current_snapshot !== 'unknown' ? `The ${sandbox.current_snapshot} snapshot provides the analysis baseline.` : 'A configured baseline keeps each analysis isolated.'} Source outcomes show what was actually captured.</p></div><div><FolderOpen size={19} aria-hidden="true"/><h3>Evidence you can inspect</h3><p>Review recorded behavior and download the files retained for a session.</p></div></div>
  </>;
}

export function SessionsPage() {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setOffset(0); }, 250); return () => clearTimeout(timer); }, [search]);
  const result = useResource<Page<ArchiveItem>>(`/console/sessions?offset=${offset}&limit=25&q=${encodeURIComponent(query)}&mode=${filter}`, 10000);
  return <><Heading eyebrow="ANALYSIS ARCHIVE" title="Sessions">Recorded analyses, source outcomes, and retained evidence.</Heading><Panel><div className="table-toolbar"><label className="search-field"><Search size={17} aria-hidden="true"/><span className="sr-only">Search sessions</span><input placeholder="Search by sample or session ID" value={search} onChange={e => setSearch(e.target.value)}/></label><label className="filter-field"><span className="sr-only">Filter by origin</span><select value={filter} onChange={e => { setFilter(e.target.value); setOffset(0); }}><option value="all">All origins</option><option value="live">Live analysis</option><option value="simulation">Simulation</option><option value="replay">Synthetic replay</option></select></label><Button compact onClick={result.refresh} aria-label="Refresh sessions"><RefreshCw size={16}/></Button></div>{result.error && <Notice error>{result.error}</Notice>}{result.loading && !result.data ? <Loading/> : result.data?.items.length ? <SessionTable items={result.data.items}/> : <Empty title="No matching sessions">Try another search or launch a new analysis.</Empty>}<Pager offset={offset} total={result.data?.total || 0} size={25} onChange={setOffset}/></Panel></>;
}

function Pager({ offset, total, size = 100, onChange }: { offset: number; total: number; size?: number; onChange: (offset: number) => void }) {
  return <div className="pager"><span className="mono">{total ? `${number(offset + 1)}–${number(Math.min(offset + size, total))} of ${number(total)}` : '0 records'}</span><div><Button compact disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - size))} aria-label="Previous page"><ChevronLeft size={16}/></Button><Button compact disabled={offset + size >= total} onClick={() => onChange(offset + size)} aria-label="Next page"><ChevronRight size={16}/></Button></div></div>;
}

const keepPollingSummary = (summary: Summary) => !terminal(summary.metadata.status);

export function SessionPage() {
  const { id = '' } = useParams();
  return <SessionView key={id} id={id}/>;
}
function SessionView({ id }: { id: string }) {
  const resource = useResource<Summary>(`/sessions/${encodeURIComponent(id)}/summary`, 5000, keepPollingSummary);
  const summary = resource.data;
  const active = !!summary && !terminal(summary.metadata.status);
  const { connection, updates } = useSessionStream(id, active, resource.refresh);
  const [, tick] = useState(0);
  useEffect(() => { if (active) { const timer = setInterval(() => tick(v => v + 1), 1000); return () => clearInterval(timer); } }, [active]);
  if (!summary) return <><Link className="back-link" to="/sessions"><ArrowLeft size={14}/>All sessions</Link><Heading title={`Session ${shortId(id)}`}/>{resource.error ? <Notice error action={<Button compact onClick={resource.refresh}>Retry</Button>}>{resource.error}</Notice> : <Loading text="Reading session summary…"/>}</>;
  const s = summary.metadata;
  return <><Link className="back-link" to="/sessions"><ArrowLeft size={14}/>All sessions</Link><Heading eyebrow={modeLabel(summary.mode).toUpperCase()} title={`Session ${shortId(id)}`} action={<span className="connection-label"><span className={`lamp ${connection.startsWith('Reconnect') ? 'lamp-off' : ''}`} aria-hidden="true"/>{connection}</span>}>Analysis results, source verification, and observed behavior.</Heading>
    {resource.error && <Notice error>Results may be stale: {resource.error}<Button compact onClick={resource.refresh}>Refresh</Button></Notice>}
    <div className="session-strip"><div><span>Sample</span><strong>{s.sample.filename}</strong></div><div><span>Arm</span><strong>{s.arm === 'CONTROL' ? 'Control' : 'Treatment'}</strong></div><div><span>Duration {active ? '· elapsed' : ''}</span><strong>{duration(s.started_at, s.ended_at)}</strong></div><div><span>Status</span><Status value={s.status}/></div></div>
    {summary.provisional && <Notice>{summary.phase === 'Finalizing' ? 'Finalizing analysis: cleanup and persistence must finish before the final status is available.' : s.status === 'PREPARING' ? 'Preparing the sandbox. The guest starts automatically; its startup deadline is separate from the workload timeout.' : 'Analysis in progress. Live counts are provisional until cleanup and persistence finish.'}</Notice>}
    {s.error && <Notice error><strong>{s.status === 'PARTIAL' ? 'Partial analysis' : 'Analysis did not complete'}</strong><p>{s.error}</p><Link className="text-link" to="/">Return to launch controls <ArrowRight size={14}/></Link></Notice>}
    <div className="metric-grid">{[['Raw records', s.metrics.raw_events], ['Behavioral events', s.metrics.semantic_events], ['Policy decisions', s.metrics.decisions_total], ['Applied mutations', s.metrics.mutations_applied]].map(([label, value]) => <div key={String(label)} className="metric"><span>{label}</span><strong>{summary.provisional && label === 'Raw records' ? 'Pending' : number(value as number)}</strong></div>)}</div>
    <div className="session-grid"><div className="session-primary"><TelemetryPanel id={id} active={active}/><Panel title="ATT&CK coverage" action={<span className="quiet-label">{summary.attck.length} techniques</span>}>{summary.attck.length ? <div className="techniques">{summary.attck.map(t => <div key={`${t.technique}-${t.tactic}`}><strong>{t.technique}</strong><span>{t.tactic || 'Tactic unspecified'}</span></div>)}</div> : <Empty title="No mapped techniques">No ATT&amp;CK techniques were recorded for this session.</Empty>}</Panel>{summary.indicators.length > 0 && <Panel title="Observed indicators" action={<span className="quiet-label">{summary.indicators_truncated ? 'First 100' : summary.indicators.length} targets</span>}><div className="table-scroll evidence-scroll" role="region" aria-label="Observed indicators" tabIndex={0}><table><thead><tr><th>Origin</th><th>Recorded target</th></tr></thead><tbody>{summary.indicators.map((i, index) => <tr key={`${i.source}-${i.target}-${index}`}><td>{i.source}{i.status && <span className="table-secondary">{i.status}</span>}</td><td className="mono wrap-target">{i.target}</td></tr>)}</tbody></table></div></Panel>}</div>
    <div className="session-secondary"><Panel title="Sample metadata"><dl className="metadata-list"><div><dt>SHA256</dt><dd><CopyValue value={s.sample.sha256}/></dd></div><div><dt>Size</dt><dd className="mono">{number(s.sample.size_bytes)} bytes</dd></div><div><dt>Type</dt><dd>{s.sample.file_type}</dd></div><div><dt>Sandbox</dt><dd>{s.config.vm_profile}</dd></div><div><dt>Network</dt><dd>{s.config.network_mode}</dd></div><div><dt>Workload limit</dt><dd>{s.config.timeout_seconds} seconds</dd></div><div><dt>Started</dt><dd>{time(s.started_at)}</dd></div><div><dt>Process exit</dt><dd>{s.execution_exit_code == null ? 'Not available' : s.execution_exit_code}</dd></div></dl></Panel><Lifecycle session={s} phase={summary.phase}/><Sources session={s}/>{active && updates.length > 0 && <Panel title="Live observations"><ul className="live-updates">{updates.slice(0, 5).map((entry, index) => <li key={`${entry}-${index}`}>{entry.replaceAll('_', ' ')}</li>)}</ul><p className="small subdued">Recent stream updates. The timeline reads persisted records.</p></Panel>}{summary.comparison_available && <Panel title="Paired experiment"><p className="small subdued">This session belongs to a control and treatment pair.</p><Link className="text-link" to={`/experiments/${s.experiment_id}`}>Open comparison <ArrowRight size={14}/></Link></Panel>}</div></div>
    <Exports summary={summary}/>
  </>;
}

function Lifecycle({ session, phase }: { session: AnalysisSession; phase?: string }) {
  const state = !terminal(session.status) && phase ? phase : session.status.toLowerCase().replaceAll('_', ' ');
  const entries = [`Started · ${time(session.started_at)}`, state.charAt(0).toUpperCase() + state.slice(1)];
  if (session.ended_at) entries.push(`Ended · ${time(session.ended_at)}`);
  return <Panel title="Lifecycle"><ol className="lifecycle">{entries.map((label, index) => <li key={index} className="reached"><span aria-hidden="true"/>{label}</li>)}</ol></Panel>;
}

function Sources({ session }: { session: AnalysisSession }) {
  return <Panel title="Capture sources"><div className="source-list">{[['sysmon', 'Sysmon'], ['procmon', 'Procmon'], ['network', 'Network']].map(([key, label]) => { const outcome = session.source_outcomes?.[key]; const errors = outcome?.errors || []; const state = !outcome ? 'Not reported' : outcome.enabled === false ? 'Disabled' : errors.length ? 'With errors' : outcome.parsed ? 'Captured' : outcome.capture_started ? 'Started' : outcome.verified ? 'Ready' : 'Not started'; return <div className="source" key={key}><div><strong>{label}</strong><span>{state}</span></div><dl><div><dt>Records</dt><dd>{number(outcome?.events_count)}</dd></div><div><dt>Live</dt><dd>{number(outcome?.live_events_count)}</dd></div></dl>{outcome && <div className="source-checks">{[['Verified', outcome.verified], ['Exported', outcome.exported], ['Parsed', outcome.parsed]].map(([name, ok]) => <span key={String(name)} className={ok ? 'checked' : ''}>{ok ? <CheckCircle2 size={11}/> : <span aria-hidden="true">—</span>}{name}</span>)}</div>}{errors.map((error, i) => <p className="source-error" key={i}>{error}</p>)}</div>; })}</div>{!!session.failures?.length && <details className="failure-details" open><summary>Recorded failures ({session.failures.length})</summary>{session.failures.map((f, i) => <div key={i}><strong>{f.stage} · {f.code}</strong><p>{f.description}</p></div>)}</details>}</Panel>;
}

function TelemetryPanel({ id, active }: { id: string; active: boolean }) {
  const [kind, setKind] = useState('events'); const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<RecordItem | null>(null);
  const page = useResource<Page<RecordItem>>(`/sessions/${id}/telemetry/${kind}?offset=${offset}&limit=100`, active ? 5000 : 0);
  return <Panel title="Event timeline" action={<Button compact onClick={page.refresh} aria-label="Refresh timeline"><RefreshCw size={14}/></Button>}>
    <div className="tabs" role="group" aria-label="Telemetry kind">{[['events', 'Behavior'], ['decisions', 'Decisions'], ['mutations', 'Mutations']].map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setOffset(0); }}>{label}</button>)}</div>
    {page.error && <Notice error>{page.error}</Notice>}{page.loading && !page.data ? <Loading text="Reading telemetry…"/> : page.data?.items.length ? <div className="table-scroll timeline" tabIndex={0} role="region" aria-label={`${kind} timeline`}><table><thead><tr><th>Time</th><th>{kind === 'events' ? 'Observed behavior' : kind === 'decisions' ? 'Policy decision' : 'Mutation'}</th><th>Details</th></tr></thead><tbody>{page.data.items.map(record => {
      const event = 'semantic_id' in record ? record : null;
      const decision = 'decision_id' in record && 'verdict' in record ? record : null;
      const mutation = 'mutation_id' in record ? record : null;
      const recordId = event?.semantic_id || decision?.decision_id || mutation?.mutation_id;
      const timestamp = event?.window_start || decision?.decided_at || mutation?.applied_at || '';
      const title = event?.intent || decision?.action || mutation?.primitive || 'Recorded decision';
      return <tr key={recordId}><td className="mono nowrap" title={timestamp}>{new Date(timestamp).toLocaleTimeString(undefined, { hour12: false })}</td><td><strong className="event-title">{title.replaceAll('_', ' ')}</strong><span className="table-secondary">{event ? `${event.actor?.image?.split('\\').pop() || 'Unknown process'} · ${Math.round(event.confidence * 100)}% confidence` : decision ? decision.verdict.replaceAll('_', ' ') : mutation?.status}</span></td><td><Button compact onClick={() => setSelected(record)} aria-label={`Inspect ${title}`}>Inspect</Button></td></tr>;
    })}</tbody></table></div> : <Empty title="No records yet">{active ? 'Recorded activity will appear as the analysis proceeds.' : 'No records of this type were retained for this session.'}</Empty>}
    <Pager offset={offset} total={page.data?.total || 0} onChange={setOffset}/>
    <Dialog open={!!selected} title="Recorded event details" onClose={() => setSelected(null)}><p className="small subdued">Original stored fields for this record.</p><pre className="record-json">{JSON.stringify(selected, null, 2)}</pre></Dialog>
  </Panel>;
}

function Exports({ summary }: { summary: Summary }) {
  const [format, setFormat] = useState('html'); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const id = summary.metadata.session_id;
  async function download(path: string, filename: string) {
    setBusy(true); setError(null);
    try {
      const response = await fetch(`${base}${path}`);
      if (!response.ok) throw new Error(`Download unavailable (${response.status}). The file may no longer be retained.`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(err instanceof Error ? err.message : 'Download failed'); }
    finally { setBusy(false); }
  }
  return <Panel className="exports"><div className="export-heading"><div><span className="eyebrow">RETAINED EVIDENCE</span><h2>Reports &amp; artifacts</h2></div><div className="report-action"><label className="sr-only" htmlFor="report-format">Report format</label><select id="report-format" value={format} onChange={e => setFormat(e.target.value)}><option value="html">HTML report</option><option value="json">JSON report</option><option value="md">Markdown report</option></select><Button compact disabled={busy || !terminal(summary.metadata.status)} onClick={async () => { setBusy(true); setError(null); try { const response = await fetch(`${base}/api/v1/sessions/${id}/report?format=${format}`); if (!response.ok) throw new Error(`Report unavailable (${response.status})`); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `${id}-report.${format}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); } catch (err) { setError(err instanceof Error ? err.message : 'Download failed'); } finally { setBusy(false); } }}><Download size={14}/>{busy ? 'Preparing…' : 'Download report'}</Button></div></div>
    {summary.artifacts.length ? <div className="artifact-grid">{summary.artifacts.map(a => <a className="artifact-link" key={a.kind} href={`${base}${a.url}`} download onClick={e => { e.preventDefault(); void download(a.url, a.filename); }}><File size={18} aria-hidden="true"/><div><strong>{a.filename}</strong><span className="mono">{number(a.size_bytes)} bytes</span></div><Download size={14} aria-hidden="true"/></a>)}</div> : <p className="subdued small">{terminal(summary.metadata.status) ? 'No capture artifact files were located for this session.' : 'Available evidence files will be listed after capture and export.'}</p>}{error && <Notice error>{error}</Notice>}
  </Panel>;
}

export function SimulationPage() {
  const navigate = useNavigate(); const { refresh } = useWorkspace();
  const [file, setFile] = useState<File | null>(null); const [paired, setPaired] = useState(false); const [treatment, setTreatment] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  async function run(replay = false) {
    setBusy(true); setError(null);
    try {
      const body = new FormData(); if (file) body.append('file', file);
      const result = replay ? await request<{ session_id: string }>('/sessions/replay', { method: 'POST' }) : paired ? await request<{ experiment_id: string }>('/experiments/run', { method: 'POST', body }) : await request<{ session_id: string }>(`/sessions/simulate?deception_enabled=${treatment}`, { method: 'POST', body });
      refresh(); navigate('experiment_id' in result ? `/experiments/${result.experiment_id}` : `/sessions/${result.session_id}`);
    } catch (err) { setError(err instanceof Error ? err.message : 'Simulation failed'); } finally { setBusy(false); }
  }
  return <><Heading eyebrow="SYNTHETIC ENVIRONMENT" title="Simulation">Explore deterministic behavior and compare policy outcomes.</Heading><Notice>Simulation uses generated events. Uploaded bytes provide a deterministic seed; no VM capture is performed.</Notice><div className="simulation-grid"><Panel title="Configure simulation"><div className="simulation-diagram" aria-label="Simulation flow"><div><File size={24}/><strong>Sample seed</strong></div><ArrowRight size={18} aria-hidden="true"/><div><Radio size={24}/><strong>Event stream</strong></div><ArrowRight size={18} aria-hidden="true"/><div><ShieldCheck size={24}/><strong>Policy outcomes</strong></div></div><div className="field"><label htmlFor="simulation-file">Seed file</label><input type="file" id="simulation-file" onChange={e => setFile(e.target.files?.[0] || null)} aria-describedby="seed-description"/><p id="seed-description" className="small subdued">The same file produces the same input event sequence.</p></div><div className="form-grid form-grid-two"><div className="field"><label htmlFor="simulation-mode">Run type</label><select id="simulation-mode" value={paired ? 'paired' : 'single'} onChange={e => setPaired(e.target.value === 'paired')}><option value="single">Single simulation</option><option value="paired">Control + treatment pair</option></select></div><div className="field"><label htmlFor="simulation-arm">Arm</label><select id="simulation-arm" disabled={paired} value={treatment ? 'treatment' : 'control'} onChange={e => setTreatment(e.target.value === 'treatment')}><option value="control">Control</option><option value="treatment">Treatment</option></select></div></div><div className="launch-bottom"><p className="small subdued">Results are labeled Simulation throughout the workspace.</p><Button primary disabled={busy || !file} onClick={() => void run()}><Play size={15}/>{busy ? 'Generating…' : 'Run simulation'}</Button></div></Panel><Panel title="Synthetic replay" className="replay-panel"><FlaskConical className="replay-icon" size={34}/><h3>A repeatable demonstration</h3><p>Replay ADAM’s built-in synthetic event dataset to inspect detection and policy behavior without starting a virtual machine.</p><Button disabled={busy} onClick={() => void run(true)}><Play size={15}/>Run synthetic replay</Button><span className="small subdued">Generated data · clearly labeled</span></Panel></div>{error && <Notice error>{error}</Notice>}</>;
}

interface Comparison { experiment_id: string; control_session: string; treatment_session: string; delta_semantic_events: number; distinct_intents_yield: string[]; distinct_networks_yield: string[] }
export function ComparisonPage() {
  const { id } = useParams(); const navigate = useNavigate(); const { archive } = useWorkspace();
  const pairs = new Map<string, Set<string>>();
  for (const { metadata: s } of archive?.items || []) { if (!pairs.has(s.experiment_id)) pairs.set(s.experiment_id, new Set()); pairs.get(s.experiment_id)!.add(s.arm); }
  const choices = [...pairs].filter(([, arms]) => arms.has('CONTROL') && arms.has('TREATMENT')).map(([experiment]) => experiment);
  return <><Heading eyebrow="CONTROL / TREATMENT" title="Comparison">Compare recorded behavior and outcomes between paired analyses.</Heading><Panel><div className="field comparison-select"><label htmlFor="experiment-select">Paired experiment</label><select id="experiment-select" value={id || ''} onChange={e => { if (e.target.value) navigate(`/experiments/${e.target.value}`); }}><option value="">Select a paired experiment</option>{id && !choices.includes(id) && <option value={id}>{id}</option>}{choices.map(choice => <option key={choice} value={choice}>{choice}</option>)}</select></div></Panel>{id ? <ComparisonResult key={id} id={id}/> : <Panel><Empty title={choices.length ? 'Choose an experiment' : 'No paired experiments yet'} action={<Link className="text-link" to="/simulate">Create a paired simulation <ArrowRight size={15}/></Link>}>A comparison requires both a control and treatment session from the same experiment.</Empty></Panel>}</>;
}
function ComparisonResult({ id }: { id: string }) {
  const [settled, setSettled] = useState(false);
  const result = useResource<Comparison>(`/experiments/${encodeURIComponent(id)}/comparison`, 5000, () => !settled);
  const { refresh } = result;
  const onSettled = useCallback(() => { setSettled(true); refresh(); }, [refresh]);
  if (result.error) return <Notice error action={<Button compact onClick={result.refresh}>Retry</Button>}>{result.error}</Notice>;
  if (!result.data) return <Loading text="Reading paired results…"/>;
  return <ComparisonArms report={result.data} onSettled={onSettled}/>;
}
function ComparisonArms({ report, onSettled }: { report: Comparison; onSettled: () => void }) {
  const control = useResource<Summary>(`/sessions/${report.control_session}/summary`, 5000, keepPollingSummary);
  const treatment = useResource<Summary>(`/sessions/${report.treatment_session}/summary`, 5000, keepPollingSummary);
  const controlStatus = control.data?.metadata.status; const treatmentStatus = treatment.data?.metadata.status;
  useEffect(() => { if (terminal(controlStatus) && terminal(treatmentStatus)) onSettled(); }, [controlStatus, treatmentStatus, onSettled]);
  if (control.error || treatment.error) return <Notice error>{control.error || treatment.error}</Notice>;
  if (!control.data || !treatment.data) return <Loading text="Reading arm summaries…"/>;
  const c = control.data.metadata; const t = treatment.data.metadata;
  const metrics: [string, number, number][] = [['Raw records', c.metrics.raw_events, t.metrics.raw_events], ['Behavioral events', c.metrics.semantic_events, t.metrics.semantic_events], ['Policy decisions', c.metrics.decisions_total, t.metrics.decisions_total], ['Mutation attempts', control.data.counts.mutations, treatment.data.counts.mutations], ['Applied mutations', c.metrics.mutations_applied, t.metrics.mutations_applied], ['ATT&CK techniques', control.data.attck.length, treatment.data.attck.length]];
  return <><Panel title="Recorded outcomes"><div className="table-scroll" tabIndex={0} role="region" aria-label="Control and treatment comparison"><table className="comparison-table"><thead><tr><th>Metric</th><th><span>Control</span><Link to={`/sessions/${c.session_id}`}>{shortId(c.session_id)} <ArrowUpRight size={12}/></Link></th><th><span>Treatment</span><Link to={`/sessions/${t.session_id}`}>{shortId(t.session_id)} <ArrowUpRight size={12}/></Link></th><th>Delta</th></tr></thead><tbody><tr><th>Status</th><td><Status value={c.status}/></td><td><Status value={t.status}/></td><td>—</td></tr>{metrics.map(([label, a, b]) => <tr key={label}><th>{label}</th><td className="mono">{number(a)}</td><td className="mono">{number(b)}</td><td className="mono delta">{b > a ? '+' : ''}{number(b - a)}</td></tr>)}<tr><th>Duration</th><td className="mono">{duration(c.started_at, c.ended_at)}</td><td className="mono">{duration(t.started_at, t.ended_at)}</td><td>—</td></tr></tbody></table></div></Panel><div className="comparison-yield"><Panel title="Additional observed intents">{report.distinct_intents_yield.length ? <ul className="yield-list">{report.distinct_intents_yield.map(intent => <li key={intent}>{intent.replaceAll('_', ' ')}</li>)}</ul> : <p className="small subdued">No additional distinct intents were reported in the treatment’s post-mutation window.</p>}</Panel><Panel title="Additional network targets">{report.distinct_networks_yield.length ? <ul className="yield-list mono">{report.distinct_networks_yield.map(target => <li key={target}>{target}</li>)}</ul> : <p className="small subdued">No additional distinct network targets were reported.</p>}</Panel></div><p className="small subdued">Deltas show treatment minus control. Generated experiments remain simulation evidence; results describe observed behavior rather than a malware verdict.</p></>;
}
