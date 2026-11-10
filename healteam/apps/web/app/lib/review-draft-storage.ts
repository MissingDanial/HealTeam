import { useCallback, useMemo, useSyncExternalStore } from "react";
import type { DimensionJudgment, MilestoneJudgment, ReviewSubmission } from "./evaluation-types";

export interface ReviewFormDraft {
  reviewer_name: string;
  dimensions: DimensionJudgment[];
  milestones: MilestoneJudgment[];
  notes: string;
  base_revision: number;
  pending: ReviewSubmission | null;
}
const memory = new Map<string, string | null>();
const changed = "healteam:review-draft-changed";
export const reviewDraftKey = (sessionId: string, reportHash: string) => `healteam:review-draft:v1:${sessionId}:${reportHash}`;
function raw(key: string) {
  if (memory.has(key)) return memory.get(key) ?? null;
  try { return window.sessionStorage.getItem(key); } catch { return null; }
}
const dimensions = new Set(["participation", "exploration", "school_consistency", "collaborative_next_step"]);
const fingerprint = /^[0-9a-f]{64}$/;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function text(value: unknown, maximum: number): value is string { return typeof value === "string" && value.length <= maximum; }
function evidence(value: unknown): value is number[] { return Array.isArray(value) && value.length <= 12 && value.every((seq) => Number.isInteger(seq) && Number(seq) >= 1) && new Set(value).size === value.length; }
function validDimensions(value: unknown): value is DimensionJudgment[] {
  return Array.isArray(value) && value.length === 4 && value.every((item) => record(item) && typeof item.dimension_id === "string" && dimensions.has(item.dimension_id) && (item.rating === null || item.rating === 0 || item.rating === 1 || item.rating === 2) && text(item.rationale, 1200) && evidence(item.evidence_seq)) && new Set(value.map((item: DimensionJudgment) => item.dimension_id)).size === 4;
}
function validMilestones(value: unknown): value is MilestoneJudgment[] {
  return Array.isArray(value) && value.length <= 20 && value.every((item) => record(item) && typeof item.milestone_id === "string" && /^[a-zA-Z0-9_-]{1,64}$/.test(item.milestone_id) && ["observed", "not_observed", "insufficient_evidence"].includes(String(item.status)) && text(item.rationale, 1200) && evidence(item.evidence_seq)) && new Set(value.map((item: MilestoneJudgment) => item.milestone_id)).size === value.length;
}
function validPending(value: unknown): value is ReviewSubmission {
  return record(value) && typeof value.client_review_id === "string" && uuid.test(value.client_review_id) && Number.isInteger(value.expected_revision) && Number(value.expected_revision) >= 0 && typeof value.report_hash === "string" && fingerprint.test(value.report_hash) && typeof value.rubric_hash === "string" && fingerprint.test(value.rubric_hash) && text(value.reviewer_name, 80) && value.reviewer_name.trim().length > 0 && validDimensions(value.dimensions) && validMilestones(value.milestones) && text(value.notes, 2000);
}
export function readReviewDraft(key: string): ReviewFormDraft | null {
  try {
    const value: unknown = JSON.parse(raw(key) ?? "null");
    if (!record(value) || !text(value.reviewer_name, 80) || !validDimensions(value.dimensions) || !validMilestones(value.milestones) || !text(value.notes, 2000) || !Number.isInteger(value.base_revision) || Number(value.base_revision) < 0 || (value.pending !== null && !validPending(value.pending))) return null;
    return { reviewer_name: value.reviewer_name, dimensions: value.dimensions, milestones: value.milestones, notes: value.notes, base_revision: Number(value.base_revision), pending: value.pending };
  } catch { return null; }
}
export function writeReviewDraft(key: string, value: ReviewFormDraft | null) {
  const text = value ? JSON.stringify(value) : null;
  if (raw(key) === text) return;
  memory.set(key, text);
  try { if (text) window.sessionStorage.setItem(key, text); else window.sessionStorage.removeItem(key); } catch { /* The current tab still retains its memory draft. */ }
  window.dispatchEvent(new Event(changed));
}
export function resolveReviewDraft(key: string, requestId: string) {
  if (readReviewDraft(key)?.pending?.client_review_id === requestId) writeReviewDraft(key, null);
}
function subscribe(listener: () => void) {
  window.addEventListener(changed, listener);
  return () => window.removeEventListener(changed, listener);
}
const server = () => null;
export function useReviewDraft(key: string, fallback: ReviewFormDraft) {
  const snapshot = useCallback(() => raw(key), [key]);
  const text = useSyncExternalStore(subscribe, snapshot, server);
  return useMemo(() => {
    try { return text ? readReviewDraft(key) ?? fallback : fallback; } catch { return fallback; }
  }, [key, text, fallback]);
}
