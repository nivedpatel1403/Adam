import type { AnalysisSession, SemanticEvent, PolicyDecision, MutationResult } from '../api/types';

export const base = import.meta.env.VITE_API_BASE_URL || '';
export type RecordItem = SemanticEvent | PolicyDecision | MutationResult;
export type Mode = 'live' | 'replay' | 'simulation' | 'unknown';
export interface ArchiveItem { metadata: AnalysisSession; mode: Mode }
export interface Page<T> { items: T[]; total: number; next_offset: number | null }
export interface Sample { id: string; filename: string; size_bytes: number; sha256: string }
export interface Sandbox {
  vm_state: string; agent_healthy: boolean; current_snapshot: string; vm_name: string;
  profile: string; busy: boolean; error: string | null; readiness_timeout_seconds: number;
}
export interface Summary extends ArchiveItem {
  counts: { events: number; decisions: number; mutations: number };
  attck: { technique: string; tactic: string }[];
  indicators: { source: string; target: string; status?: string }[];
  indicators_truncated: boolean;
  artifacts: { kind: string; filename: string; size_bytes: number; url: string }[];
  comparison_available: boolean; provisional: boolean; phase?: string;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${base}/api/v1${path}`, init);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const error = await response.json();
      message = typeof error.detail === 'string' ? error.detail : error.error || message;
    } catch { /* A proxy can return an HTML error page. */ }
    throw new Error(message);
  }
  return response.json();
}

export function modeLabel(mode: Mode) {
  return { live: 'Live analysis', replay: 'Synthetic replay', simulation: 'Simulation', unknown: 'Unknown origin' }[mode];
}
export const terminal = (status?: string) => !!status && ['COMPLETED', 'PARTIAL', 'FAILED', 'ABORTED'].includes(status);
export const number = (value?: number | null) => value == null ? '—' : value.toLocaleString();
export const shortId = (id: string) => id.replace(/^sess_\d{4}_\d{2}_\d{2}_/, '').replace(/^exp_/, '');
export function time(value?: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
export function duration(start: string, end?: string | null) {
  const seconds = Math.max(0, Math.floor(((end ? Date.parse(end) : Date.now()) - Date.parse(start)) / 1000));
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`;
}
