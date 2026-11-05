export type Arm = 'CONTROL' | 'TREATMENT';
export type NetworkMode = 'HOST_ONLY' | 'SIMULATED' | 'INTERNET';
export type SessionStatus =
  | 'PENDING'
  | 'PREPARING'
  | 'RUNNING'
  | 'COMPLETED'
  | 'PARTIAL'
  | 'FAILED'
  | 'ABORTED';

export interface SampleRef {
  sha256: string;
  md5: string;
  filename: string;
  size_bytes: number;
  file_type: string;
}

export interface SessionConfig {
  deception_enabled: boolean;
  policy_ruleset: string;
  vm_profile: string;
  timeout_seconds: number;
  network_mode: NetworkMode;
}

export interface SessionMetrics {
  raw_events: number;
  semantic_events: number;
  decisions_total: number;
  decisions_executed: number;
  mutations_applied: number;
  semantic_events_post_mutation?: number;
}

export interface SourceOutcome {
  enabled?: boolean;
  required?: boolean;
  verified?: boolean;
  capture_started?: boolean;
  exported?: boolean;
  parsed?: boolean;
  events_count?: number;
  live_events_count?: number;
  errors?: string[];
  source?: string;
  status?: string;
}

export interface SessionFailure {
  stage: string;
  code: string;
  description: string;
}

export interface AnalysisSession {
  session_id: string;
  experiment_id: string;
  arm: Arm;
  sample: SampleRef;
  config: SessionConfig;
  status: SessionStatus;
  started_at: string;
  ended_at: string | null;
  metrics: SessionMetrics;
  error?: string | null;
  source_outcomes?: Record<string, SourceOutcome>;
  failures?: SessionFailure[];
  execution_pid?: number | null;
  execution_exit_code?: number | null;
}

export interface Actor {
  pid: number;
  image?: string | null;
  guid?: string | null;
}

export interface AttckRef {
  tactic: string;
  technique: string;
}

export interface SemanticEvent {
  semantic_id: string;
  session_id: string;
  correlation_id: string;
  intent: string;
  confidence: number;
  severity: string;
  window_start: string;
  window_end: string;
  actor: Actor;
  evidence: string[];
  attck?: AttckRef | null;
  detector: string;
  features: Record<string, any>;
  caused_by_mutation?: string | null;
}

export type Verdict =
  | 'EXECUTE'
  | 'SUPPRESSED_BUDGET'
  | 'SUPPRESSED_COOLDOWN'
  | 'SUPPRESSED_CONFIDENCE'
  | 'SUPPRESSED_CONFLICT'
  | 'DRY_RUN';

export interface PolicyDecision {
  decision_id: string;
  session_id: string;
  correlation_id: string;
  triggered_by: string;
  rule_id: string;
  rule_version: string;
  action?: string | null;
  verdict: Verdict;
  priority: number;
  parameters: Record<string, any>;
  rationale: string;
  decided_at: string;
  evaluation_ms: number;
}

export type MutationStatus =
  | 'APPLIED'
  | 'PARTIAL'
  | 'FAILED'
  | 'REVERTED'
  | 'SKIPPED';

export interface Change {
  kind: 'REGISTRY' | 'FILE' | 'NETWORK' | 'PROCESS' | string;
  target: string;
  operation: string;
  value?: string | null;
  value_type?: string | null;
}

export interface MutationResult {
  mutation_id: string;
  session_id: string;
  correlation_id: string;
  decision_id: string;
  primitive: string;
  status: MutationStatus;
  applied_at: string;
  latency_ms: number;
  changes: Change[];
  plausibility_score: number;
  plausibility_notes?: string;
  revertible: boolean;
  causal_window_ms: number;
  error?: string | null;
}

export interface SessionDetailResponse {
  metadata?: AnalysisSession | null;
  events: SemanticEvent[];
  decisions: PolicyDecision[];
  mutations: MutationResult[];
}

export interface SandboxStatus {
  vm_state: 'Running' | 'Booting' | 'Off' | 'unknown' | string;
  agent_healthy: boolean;
  current_snapshot: string;
}

export interface ExperimentComparison {
  experiment_id: string;
  control_session?: string;
  treatment_session?: string;
  [key: string]: any;
}

