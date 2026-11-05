import type {
  SandboxStatus,
  SessionDetailResponse,
  ExperimentComparison,
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const errorText = await res.text();
    let parsed: any;
    try {
      parsed = JSON.parse(errorText);
    } catch {
      parsed = null;
    }
    const message = (parsed && parsed.error) || errorText || `HTTP ${res.status}`;
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export async function fetchSessions(): Promise<string[]> {
  const data = await handleResponse<{ sessions: string[] }>(
    await fetch(`${API_BASE}/api/v1/sessions`)
  );
  return data.sessions || [];
}

export async function fetchSessionDetail(sessionId: string): Promise<SessionDetailResponse> {
  return handleResponse<SessionDetailResponse>(
    await fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}`)
  );
}

export async function fetchSessionReport(
  sessionId: string,
  format: 'json' | 'html' | 'md' = 'json'
): Promise<any> {
  const res = await fetch(
    `${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/report?format=${format}`
  );
  if (!res.ok) {
    throw new Error(`Report fetch failed: ${res.statusText}`);
  }
  if (format === 'json') {
    return res.json();
  }
  return res.text();
}

export async function fetchExperimentComparison(
  experimentId: string
): Promise<ExperimentComparison> {
  return handleResponse<ExperimentComparison>(
    await fetch(
      `${API_BASE}/api/v1/experiments/${encodeURIComponent(experimentId)}/comparison`
    )
  );
}

export async function fetchSandboxStatus(): Promise<SandboxStatus> {
  return handleResponse<SandboxStatus>(
    await fetch(`${API_BASE}/api/v1/sandbox/status`)
  );
}

export async function resetSandbox(): Promise<{ status: string }> {
  return handleResponse<{ status: string }>(
    await fetch(`${API_BASE}/api/v1/sandbox/reset`, { method: 'POST' })
  );
}

export async function triggerLiveDetonation(
  file: File,
  profile: string,
  deceptionEnabled: boolean
): Promise<{ status: string; sample: string }> {
  const formData = new FormData();
  formData.append('file', file);

  const url = `${API_BASE}/api/v1/sessions/live?profile=${encodeURIComponent(
    profile
  )}&deception_enabled=${deceptionEnabled ? 'true' : 'false'}`;

  return handleResponse<{ status: string; sample: string }>(
    await fetch(url, {
      method: 'POST',
      body: formData,
    })
  );
}

export async function triggerReplay(): Promise<{ session_id: string; status: string }> {
  return handleResponse<{ session_id: string; status: string }>(
    await fetch(`${API_BASE}/api/v1/sessions/replay`, { method: 'POST' })
  );
}

export async function triggerSimulation(
  file: File,
  deceptionEnabled: boolean = true,
  experimentId?: string
): Promise<{ session_id: string; status: string }> {
  const formData = new FormData();
  formData.append('file', file);

  let url = `${API_BASE}/api/v1/sessions/simulate?deception_enabled=${deceptionEnabled ? 'true' : 'false'}`;
  if (experimentId) {
    url += `&experiment_id=${encodeURIComponent(experimentId)}`;
  }

  return handleResponse<{ session_id: string; status: string }>(
    await fetch(url, {
      method: 'POST',
      body: formData,
    })
  );
}

export async function triggerExperiment(
  file: File
): Promise<{ experiment_id: string; control_session: string; treatment_session: string }> {
  const formData = new FormData();
  formData.append('file', file);

  return handleResponse<{
    experiment_id: string;
    control_session: string;
    treatment_session: string;
  }>(
    await fetch(`${API_BASE}/api/v1/experiments/run`, {
      method: 'POST',
      body: formData,
    })
  );
}

export function getArtifactDownloadUrl(sessionId: string, kind: 'raw' | 'sysmon'): string {
  return `${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/artifacts/${kind}`;
}
