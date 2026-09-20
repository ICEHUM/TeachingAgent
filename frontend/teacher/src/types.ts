export type ClassroomItem = { attempt_id: string; student: string; stage: string; stage_key: string; reason: string; failure_count: number; help_level: string; wait_seconds: number; intervention_id: string | null; intervention_status: string | null; category: string; ai_guidance_paused: boolean };
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
};
