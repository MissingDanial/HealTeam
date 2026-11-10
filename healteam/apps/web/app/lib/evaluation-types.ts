import type { Actor, School } from "./types";

export type DimensionId = "participation" | "exploration" | "school_consistency" | "collaborative_next_step";
export type Rating = 0 | 1 | 2 | null;
export type MilestoneStatus = "observed" | "not_observed" | "insufficient_evidence";
export interface DimensionJudgment { dimension_id: DimensionId; rating: Rating; rationale: string; evidence_seq: number[] }
export interface MilestoneJudgment { milestone_id: string; status: MilestoneStatus; rationale: string; evidence_seq: number[] }
export interface TrialRubric {
  rubric_id: string;
  rubric_version: string;
  status: "trial_unvalidated";
  sha256: string;
  notice: string;
  school: School;
  dimensions: { dimension_id: DimensionId; label: string; description: string; anchors: Record<string, string> }[];
  milestones: { milestone_id: string; label: string; description: string; required_actors: Actor[]; validation_signals?: string[] }[];
  missing_evidence_rule: string;
  milestone_rule: string;
}
export interface Assessment {
  rubric_version: string;
  rubric_hash: string;
  dimensions: DimensionJudgment[];
  milestones: MilestoneJudgment[];
  reviewed_event_seq: number[];
  total_utterances: number;
}
export interface ReviewSubmission {
  client_review_id: string;
  expected_revision: number;
  report_hash: string;
  rubric_hash: string;
  reviewer_name: string;
  dimensions: DimensionJudgment[];
  milestones: MilestoneJudgment[];
  notes: string;
}
export interface ReviewRecord extends Omit<ReviewSubmission, "expected_revision"> {
  review_id: string;
  session_id: string;
  revision: number;
  created_at: string;
}
export interface ReviewHistory { session_id: string; report_hash: string; rubric_hash: string; latest_revision: number; items: ReviewRecord[] }
export interface CalibrationGroup {
  rubric_hash: string;
  rubric_version: string;
  school: School;
  provider: string | null;
  model: string | null;
  reviewed_sessions: number;
  paired_dimensions: number;
  exact_matches: number;
  exact_agreement: number | null;
  insufficient_pairs: number;
  out_of_window_pairs: number;
}
export interface CalibrationSummary {
  status: "awaiting_expert_calibration";
  total_sessions: number;
  eligible_sessions: number;
  reviewed_sessions: number;
  paired_dimensions: number;
  exact_matches: number;
  exact_agreement: number | null;
  insufficient_pairs: number;
  out_of_window_pairs: number;
  excluded_demo_sessions: number;
  groups: CalibrationGroup[];
}
export function ratingLabel(rating: Rating): string { return rating === null ? "证据不足" : `${rating} / 2`; }
export const MILESTONE_LABELS: Record<MilestoneStatus, string> = { observed: "观察到", not_observed: "未观察到", insufficient_evidence: "证据不足" };
