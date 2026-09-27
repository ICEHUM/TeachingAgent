export type Requirement = {
  id: string; key: string; name: string; kind: string; status: string;
  student_goal?: string; success_criteria?: string;
  snapshot_id: string | null; snapshot_label: string | null; operation_id: string | null;
  evaluator: string; min_length?: number | null; evidence_refs: string[]; evaluated_at: string | null; has_old_result: boolean;
};

export type Guidance = {
  level?: "L0" | "L1" | "L2"; message?: string; next_step?: string; success?: boolean;
  fallback_reason?: string | null; latency_ms?: number; evidence_refs?: string[];
};

export type Workbench = {
  identity: { user_id: string; display_name: string };
  course: { code: string; name: string };
  task: { key: string; title: string; version: string };
  attempt: { id: string; mode: string; status: string; state_version: number; student_failure_count: number; infrastructure_failure_count: number; ai_guidance_paused: boolean };
  stage: { id: string; key: string; title: string; position: number; total: number; objective: string };
  stages: Array<{ key: string; title: string; position: number; status: string }>;
  requirements: Requirement[];
  requirement_summary: { satisfied: boolean; satisfied_count: number; required_count: number };
  snapshots: Array<{ id: string; sequence: number; label: string; created_at: string }>;
  latest_snapshot: { id: string; sequence: number; label: string; created_at: string } | null;
  files: Array<{ path: string; name: string; size: number }>;
  guidance: Guidance | null;
  guidance_generated_at?: string | null;
  guidance_snapshot?: { id: string; label: string } | null;
  student_observation: string;
  help_requested?: boolean;
  teacher_feedback?: { message: string; time: string; source?: "teacher" | "system" } | null;
  intervention: { id: string; status: string; reason: string; created_at: string } | null;
  timeline: Array<{ time: string; label: string; state_version: number }>;
  agent_trace: Array<{ kind: string; label: string; detail: string }>;
  self_service_tools: SelfServiceTool[];
  latest_submission?: {
    id: string; sequence: number; snapshot_id: string; snapshot_label: string | null;
    created_at: string; review_status: string;
    formal_grade: { total_score: number; max_score: number; published_at: string } | null;
  } | null;
};

export type RubricItem = {
  key: string; title: string; max_score: number;
  requirements: Array<{ key: string; name: string; kind: string; status: string; operation_id: string | null; evaluator: string }>;
  auto: { status: string; summary: string };
  ai: { text: string; evidence_refs: string[]; score: null };
  teacher: { score: number | null; reason: string; confirmed: boolean; status: string };
};

export type Evaluation = {
  submission: {
    id: string | null; sequence: number; status: string; snapshot_id: string; snapshot_label: string;
    created_at: string; explanation: string;
    assistance: Array<{ time: string; level?: string; message?: string; next_step?: string; success?: boolean; fallback_reason?: string | null }>;
  };
  attempt: { id: string; state_version: number; status: string; stage: string };
  student: { id: string; display_name: string };
  variant: { key: string; name: string; status: string };
  rubric: RubricItem[];
  review: { id: string | null; status: string; can_publish: boolean; missing: string[]; published_at: string | null };
  formal_grade: { total_score: number; max_score: number; published_at: string; published_by: string; change_reason: string } | null;
  files?: Array<{ path: string; name: string; size: number }>;
};

export type FilePayload = { path: string; content: string; hash: string; size: number };
export type Evidence = {
  snapshot: string; operation: string; tool: string; requirement: string; status: string;
  reason_code: string; observed_at: string; stdout_summary: string; stdout_truncated: boolean;
  checks?: Array<{ code: string; passed: boolean; detail?: string; diagnosis_code?: string }>;
  skill_evidence?: Array<{ skill_id: string; status: string; case_code: string }>;
  error_summary?: string;
  location?: { file: string; line: number } | null;
  artifacts: Array<{ kind: string; ref: string; available: boolean }>;
};

export type SelfServiceTool = { name: string; label: string };

export type ConsoleRun = {
  tool: string; label: string;
  status: "succeeded" | "student_failure" | "infrastructure_failure" | string;
  code: string; exit_code: number | null;
  stdout: string; stdout_truncated: boolean;
  stderr: string; stderr_truncated: boolean;
  location?: { file: string; line: number } | null;
  snapshot: string; snapshot_id: string | null; operation: string; recorded: boolean;
  sample_input?: string | null;
  trace?: Array<{ file: string; line: number; locals: Record<string, string> }>;
  trace_truncated?: boolean;
  state_version?: number;
};

export type TerminalSession = {
  session_id: string;
  snapshot_id: string;
  snapshot: string;
  status: "running" | "stopping" | "completed" | "failed" | "stopped" | "timed_out";
  exit_code: number | null;
  events: Array<{ kind: "stdout" | "stderr" | "input" | "system"; text: string }>;
  output_truncated: boolean;
  input_bytes: number;
  stdin_closed: boolean;
};
