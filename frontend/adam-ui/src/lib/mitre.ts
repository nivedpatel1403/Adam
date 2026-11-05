export const MITRE_TACTIC_NAMES: Record<string, string> = {
  ta0043: 'Reconnaissance',
  ta0042: 'Resource Development',
  ta0001: 'Initial Access',
  ta0002: 'Execution',
  ta0003: 'Persistence',
  ta0004: 'Privilege Escalation',
  ta0005: 'Defense Evasion',
  ta0006: 'Credential Access',
  ta0007: 'Discovery',
  ta0008: 'Lateral Movement',
  ta0009: 'Collection',
  ta0011: 'Command and Control',
  ta0010: 'Exfiltration',
  ta0040: 'Impact',
};

export const TACTIC_COLOR_STYLES: Record<string, { bg: string; color: string; border: string }> = {
  discovery: { bg: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8', border: '#0284c7' },
  reconnaissance: { bg: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8', border: '#0284c7' },
  recon: { bg: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8', border: '#0284c7' },
  'credential access': { bg: 'rgba(244, 63, 94, 0.15)', color: '#fb7185', border: '#e11d48' },
  credential: { bg: 'rgba(244, 63, 94, 0.15)', color: '#fb7185', border: '#e11d48' },
  'defense evasion': { bg: 'rgba(251, 146, 60, 0.15)', color: '#fb923c', border: '#ea580c' },
  defense: { bg: 'rgba(251, 146, 60, 0.15)', color: '#fb923c', border: '#ea580c' },
  evasion: { bg: 'rgba(251, 146, 60, 0.15)', color: '#fb923c', border: '#ea580c' },
  persistence: { bg: 'rgba(129, 140, 248, 0.15)', color: '#818cf8', border: '#4f46e5' },
  'command and control': { bg: 'rgba(244, 114, 182, 0.15)', color: '#f472b6', border: '#db2777' },
  c2: { bg: 'rgba(244, 114, 182, 0.15)', color: '#f472b6', border: '#db2777' },
  'privilege escalation': { bg: 'rgba(234, 179, 8, 0.15)', color: '#facc15', border: '#ca8a04' },
  'lateral movement': { bg: 'rgba(16, 185, 129, 0.15)', color: '#34d399', border: '#059669' },
  collection: { bg: 'rgba(20, 184, 166, 0.15)', color: '#2dd4bf', border: '#0d9488' },
  exfiltration: { bg: 'rgba(239, 68, 68, 0.15)', color: '#f87171', border: '#dc2626' },
  impact: { bg: 'rgba(225, 29, 72, 0.15)', color: '#fda4af', border: '#be123c' },
  execution: { bg: 'rgba(132, 204, 22, 0.15)', color: '#a3e635', border: '#65a30d' },
};

export function getTacticBadgeStyle(tacticTechnique: string) {
  const parts = tacticTechnique.split('/');
  const rawTactic = parts[0]?.trim() || '';
  const technique = parts[1]?.trim() || '';

  const tKey = rawTactic.toLowerCase();
  const friendlyName = MITRE_TACTIC_NAMES[tKey] || rawTactic;

  const styleKey = friendlyName.toLowerCase();
  let style = { bg: 'rgba(148, 163, 184, 0.15)', color: '#cbd5e1', border: '#64748b' };

  for (const [k, v] of Object.entries(TACTIC_COLOR_STYLES)) {
    if (styleKey.includes(k) || tKey.includes(k)) {
      style = v;
      break;
    }
  }

  const label =
    friendlyName !== rawTactic && technique
      ? `${friendlyName} (${rawTactic}) / ${technique}`
      : technique
      ? `${friendlyName} / ${technique}`
      : friendlyName;

  return { label, style };
}

