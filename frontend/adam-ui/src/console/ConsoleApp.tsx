import { useState } from 'react';
import type { ReactNode } from 'react';
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom';
import { Activity, ArrowUpRight, GitCompare, FlaskConical, FolderOpen, LayoutDashboard, Menu, RefreshCw, ShieldCheck, Triangle } from 'lucide-react';
import type { ArchiveItem, Page, Sandbox } from './api';
import { request, shortId } from './api';
import { useResource } from './hooks';
import { Button, Dialog, Notice } from './ui';
import { ConsolePage, SessionsPage, SessionPage, SimulationPage, ComparisonPage } from './pages';
import { WorkspaceContext } from './context';

function Shell({ children }: { children: ReactNode }) {
  const sandbox = useResource<Sandbox>('/sandbox/status', 8000);
  const archive = useResource<Page<ArchiveItem>>('/console/sessions?limit=50', 10000);
  const [menu, setMenu] = useState(false);
  const [reset, setReset] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetMessage, setResetMessage] = useState<string | null>(null);
  const refresh = () => { sandbox.refresh(); archive.refresh(); };
  const status = sandbox.data;
  const statusError = sandbox.error || status?.error;
  const unavailable = !!sandbox.error || !status || !!status.error;
  const nav = [
    { to: '/', label: 'Console', icon: LayoutDashboard }, { to: '/sessions', label: 'Sessions', icon: FolderOpen },
    { to: '/simulate', label: 'Simulation', icon: FlaskConical }, { to: '/comparison', label: 'Comparison', icon: GitCompare },
  ];
  return <WorkspaceContext.Provider value={{ sandbox: status, sandboxError: sandbox.error, archive: archive.data, archiveError: archive.error, refresh }}>
    <a className="skip-link" href="#workspace-main">Skip to workspace</a>
    <div className="app-chassis">
      <aside className={`sidebar ${menu ? 'sidebar-open' : ''}`}>
        <div className="brand"><Link to="/" aria-label="ADAM console"><Triangle className="brand-mark" size={25} strokeWidth={3}/><strong>ADAM</strong></Link><Button compact className="mobile-menu" onClick={() => setMenu(!menu)} aria-expanded={menu} aria-controls="workspace-navigation" aria-label="Toggle navigation"><Menu size={20}/></Button></div>
        <div className="sidebar-inner" id="workspace-navigation">
          <span className="sidebar-caption">WORKSPACE</span>
          <nav aria-label="Workspace">{nav.map(({ to, label, icon: Icon }) => <NavLink key={to} end={to === '/'} to={to} onClick={() => setMenu(false)} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}><Icon size={17} aria-hidden="true"/>{label}</NavLink>)}</nav>
          <div className="sidebar-archive"><span className="sidebar-caption">RECENT ANALYSES</span>{archive.data?.items.slice(0, 5).map(({ metadata: s }) => <Link key={s.session_id} className="archive-link" to={`/sessions/${s.session_id}`} onClick={() => setMenu(false)}><span className="archive-filename">{s.sample.filename}</span><span className="mono subdued">{shortId(s.session_id)}</span></Link>)}{archive.data?.items.length === 0 && <p className="subdued small">Your analyses will appear here.</p>}{archive.error && <p className="subdued small">Session archive unavailable.</p>}</div>
          <div className="sidebar-bottom"><span className="sandbox-note"><ShieldCheck size={15} aria-hidden="true"/>Host-only sandbox</span><Button compact disabled={!status || status.busy || resetting || !!status.error} onClick={() => { setResetError(null); setReset(true); }}><RefreshCw size={14} aria-hidden="true"/>Restore baseline</Button></div>
        </div>
      </aside>
      <div className="workspace">
        <header className="status-ribbon" aria-label="System status"><div><span className={`lamp ${unavailable ? 'lamp-off' : ''}`} aria-hidden="true"/><strong>ADAM Console</strong><span className="ribbon-divider"/><span>{unavailable ? 'Status unavailable' : status.busy ? 'Analysis active' : status.vm_state === 'Running' && !status.agent_healthy ? 'Agent unavailable' : 'Ready'}</span></div><div className="ribbon-details"><span>VM <b>{status?.vm_state || 'Unknown'}</b></span><span>Agent <b>{status?.vm_state === 'Off' ? 'Not running' : status?.agent_healthy ? 'Online' : status?.busy && status?.vm_state === 'Running' ? 'Not ready' : 'Unreachable'}</b></span><span>Snapshot <b>{status?.current_snapshot || 'Unknown'}</b></span></div></header>
        <main id="workspace-main" tabIndex={-1}>
          {statusError && <Notice error action={<Button compact aria-label="Retry sandbox status" onClick={sandbox.refresh}>Retry status</Button>}>Sandbox status: {statusError}. Check VirtualBox, then retry. The workload timeout controls sample execution, not this status check.</Notice>}
          {resetMessage && <Notice>{resetMessage}</Notice>}
          {children}
        </main>
        <footer className="workspace-footer"><span><Activity size={13} aria-hidden="true"/>Adaptive Deception &amp; Analysis of Malware</span><span>Controlled environment <ArrowUpRight size={12} aria-hidden="true"/></span></footer>
      </div>
    </div>
    <Dialog open={reset} title="Restore the clean baseline?" onClose={() => { if (!resetting) setReset(false); }}>
      <p>The sandbox will shut down normally and restore its configured clean snapshot. Changes inside the current VM will be discarded. Saved analysis results stay available.</p>
      {resetError && <Notice error>{resetError}</Notice>}
      <div className="dialog-actions"><Button disabled={resetting} onClick={() => setReset(false)}>Cancel</Button><Button primary disabled={resetting} onClick={async () => { setResetting(true); setResetError(null); try { await request('/sandbox/reset', { method: 'POST' }); setReset(false); setResetMessage('Clean baseline restored. The virtual machine is powered off.'); refresh(); } catch (err) { setResetError(err instanceof Error ? err.message : 'Restore failed'); } finally { setResetting(false); } }}>{resetting ? 'Restoring…' : 'Restore baseline'}</Button></div>
    </Dialog>
  </WorkspaceContext.Provider>;
}

export default function ConsoleApp() {
  return <BrowserRouter><Shell><Routes>
    <Route path="/" element={<ConsolePage/>}/><Route path="/sessions" element={<SessionsPage/>}/>
    <Route path="/sessions/:id" element={<SessionPage/>}/><Route path="/simulate" element={<SimulationPage/>}/>
    <Route path="/comparison" element={<ComparisonPage/>}/><Route path="/experiments/:id" element={<ComparisonPage/>}/>
    <Route path="*" element={<div className="empty"><h1>Page not found</h1><Link to="/">Return to console</Link></div>}/>
  </Routes></Shell></BrowserRouter>;
}
