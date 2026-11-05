import type { SemanticEvent, MutationResult } from '../api/types';

export interface IOCItem {
  source: 'EVENT' | 'MUTATION';
  target: string;
  operation: string;
}

const INTENT_MAP: Record<string, [string, string]> = {
  RECON_USER_ARTIFACTS: ['Desktop / Documents / User Artifacts Search', 'Directory Traversal'],
  CRED_BROWSER_STORE: ['AppData\\Local\\Google\\Chrome / Login Data', 'Credential Store Read'],
  PERSIST_RUN_KEY: ['HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', 'Persistence Query'],
  C2_BEACON: ['Outbound HTTP/DNS C2 Traffic', 'Beaconing / Check-in'],
  DISCOVERY_SYS_INFO: ['System Architecture & OS Profile', 'Host Discovery'],
};

export function extractIOCs(events: SemanticEvent[], mutations: MutationResult[]): IOCItem[] {
  const seen = new Set<string>();
  const items: IOCItem[] = [];

  // Extract from events
  for (const e of events) {
    let target: string | null = null;
    let op = 'Observed Access';

    if (e.features?.target_object) {
      target = String(e.features.target_object);
      op = 'File/Registry Access';
    } else if (e.features?.TargetFilename) {
      target = String(e.features.TargetFilename);
      op = 'File Read/Write';
    } else if (e.features?.TargetObject) {
      target = String(e.features.TargetObject);
      op = 'Registry Read/Write';
    } else if (e.features?.network_endpoint) {
      target = String(e.features.network_endpoint);
      op = 'Network Connect';
    } else if (e.features?.DestinationIp) {
      target = `${e.features.DestinationIp}:${e.features.DestinationPort || ''}`;
      op = 'Network Connect';
    } else if (e.features?.QueryName) {
      target = String(e.features.QueryName);
      op = 'DNS Query';
    } else if (e.intent) {
      const mapped = INTENT_MAP[e.intent];
      if (mapped) {
        target = mapped[0];
        op = mapped[1];
      } else {
        target = `Observed Intent: ${e.intent}`;
        op = 'Attacker Behavior';
      }
    }

    if (target) {
      const key = `EVENT:${target}:${op}`;
      if (!seen.has(key)) {
        seen.add(key);
        items.push({ source: 'EVENT', target, operation: op });
      }
    }
  }

  // Extract from decoy mutations
  for (const m of mutations) {
    if (m.changes) {
      for (const c of m.changes) {
        const op = c.operation || 'SET';
        const key = `MUTATION:${c.target}:${op}`;
        if (!seen.has(key)) {
          seen.add(key);
          items.push({ source: 'MUTATION', target: c.target, operation: op });
        }
      }
    }
  }

  return items;
}
