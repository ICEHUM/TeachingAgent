export type ClassroomItem = { attempt_id: string; student: string; stage: string; stage_key: string; reason: string; failure_count: number; help_level: string; wait_seconds: number; intervention_id: string | null; intervention_status: string | null; category: string; ai_guidance_paused: boolean; submission_id?: string | null; review_status?: string | null };
export type Classroom = { teacher: { id: string; display_name: string }; groups: { attention: ClassroomItem[]; progress: ClassroomItem[]; completed: ClassroomItem[] } };
export type Requirement = { id: string; name: string; status: string; snapshot_label: string | null; evaluator: string; operation_id: string | null; evidence_refs: string[] };
export type Detail = {
  identity: { user_id: string; display_name: string };
  course: { code: string; name: string }; task: { key: string; title: string; version: string };
  attempt: { id: string; state_version: number; student_failure_count: number; ai_guidance_paused: boolean; status: string };
  stage: { title: string; position: number; total: number; objective: string };
  requirement_summary: { satisfied_count: number; required_count: number; satisfied: boolean };
  requirements: Requirement[];
  student_observation: string;
  guidance_history: Array<{ time: string; level?: string; message?: string; success?: boolean; fallback_reason?: string | null }>;
  timeline: Array<{ time: string; label: string; state_version: number }>;
  agent_trace: Array<{ kind: string; label: string; detail: string }>;
  snapshot_diff: { from: string | null; to: string | null; files_changed: number; files?: string[]; additions: number; deletions: number };
  interventions: Array<{ id: string; status: string; reason: string; requested_state_version: number; response: string | null; allow_l2: boolean; created_at: string; resolved_at: string | null }>;
  latest_submission?: { id: string; snapshot_label: string | null; review_status: string; formal_grade: { total_score: number; max_score: number; published_at: string } | null } | null;
};

export type RubricItem = {
  key: string; title: string; max_score: number;
  requirements: Array<{ key: string; name: string; kind: string; status: string; operation_id: string | null; evaluator: string }>;
  auto: { status: string; summary: string };
  ai: { text: string; evidence_refs: string[]; score: null };
  teacher: { score: number | null; reason: string; confirmed: boolean; status: string };
};

export type Evaluation = {
  submission: { id: string; sequence: number; status: string; snapshot_id: string; snapshot_label: string; created_at: string; explanation: string; assistance: Array<{ time: string; level?: string; message?: string }> };
  attempt: { id: string; state_version: number; status: string; stage: string };
  student: { id: string; display_name: string };
  variant: { key: string; name: string; status: string };
  rubric: RubricItem[];
  review: { id: string | null; status: string; can_publish: boolean; missing: string[]; published_at: string | null };
  formal_grade: { total_score: number; max_score: number; published_at: string; published_by: string; change_reason: string } | null;
  files?: Array<{ path: string; name: string; size: number }>;
};

export type Evidence = { snapshot: string; operation: string; tool: string; requirement: string; status: string; reason_code: string; observed_at: string; stdout_summary: string; stdout_truncated: boolean; artifacts: Array<{ kind: string; ref: string; available: boolean }> };
