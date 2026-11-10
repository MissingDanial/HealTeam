"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, errorMessage } from "../lib/api";
import type { DimensionId, DimensionJudgment, MilestoneJudgment, MilestoneStatus, Rating, ReviewHistory, ReviewRecord, ReviewSubmission, TrialRubric } from "../lib/evaluation-types";
import { MILESTONE_LABELS, ratingLabel } from "../lib/evaluation-types";
import { readReviewDraft, resolveReviewDraft, reviewDraftKey, useReviewDraft, writeReviewDraft } from "../lib/review-draft-storage";
import type { ReviewFormDraft } from "../lib/review-draft-storage";
import type { EventEnvelope } from "../lib/types";
import { ACTOR_LABELS, formatTime } from "../lib/types";
import { Spinner } from "./Shared";

function latestReview(history: ReviewHistory): ReviewRecord | undefined {
  return history.items.reduce<ReviewRecord | undefined>((latest, item) => !latest || item.revision > latest.revision ? item : latest, undefined);
}

function EvidencePicker({ id, events, selected, onChange, disabled }: { id: string; events: EventEnvelope[]; selected: number[]; onChange: (items: number[]) => void; disabled: boolean }) {
  return <details className="review-evidence-picker"><summary>选择原发言证据 · 已选 {selected.length} 条</summary><div className="review-evidence-options">{events.map((event) => <label className="review-evidence-option" key={event.seq} htmlFor={`${id}-${event.seq}`}><input id={`${id}-${event.seq}`} type="checkbox" checked={selected.includes(event.seq)} disabled={disabled || (!selected.includes(event.seq) && selected.length >= 12)} onChange={(change) => onChange(change.target.checked ? [...selected, event.seq].sort((a, b) => a - b) : selected.filter((seq) => seq !== event.seq))} /><span><small>事件 #{event.seq} · 第 {event.round} 轮 · {ACTOR_LABELS[event.actor]}</small>{event.payload.content}</span></label>)}</div></details>;
}

function RevisionHistory({ history, rubric, onEvidence }: { history: ReviewHistory; rubric: TrialRubric; onEvidence: (seq: number) => void }) {
  return <details className="review-history"><summary>复核修订历史 · {history.items.length} 版</summary>{[...history.items].sort((a, b) => b.revision - a.revision).map((review) => <article className="review-history-entry" key={review.review_id}><h4>修订 {review.revision} · {review.reviewer_name}</h4><time dateTime={review.created_at}>{formatTime(review.created_at)}</time><ul>{review.dimensions.map((dimension) => <li key={dimension.dimension_id}><strong>{rubric.dimensions.find((item) => item.dimension_id === dimension.dimension_id)?.label ?? dimension.dimension_id} · {ratingLabel(dimension.rating)}</strong><p>{dimension.rationale}</p><div className="review-evidence">{dimension.evidence_seq.map((seq) => <button key={seq} className="text-button" onClick={() => onEvidence(seq)}>查看对话 #{seq} ↗</button>)}</div></li>)}</ul>{review.milestones.length > 0 && <ul>{review.milestones.map((item) => <li key={item.milestone_id}><strong>{rubric.milestones.find((milestone) => milestone.milestone_id === item.milestone_id)?.label ?? item.milestone_id} · {MILESTONE_LABELS[item.status]}</strong><p>{item.rationale}</p></li>)}</ul>}{review.notes && <p>复核备注：{review.notes}</p>}</article>)}</details>;
}

function ReviewEditor({ sessionId, history, rubric, events, demo, onHistory }: { sessionId: string; history: ReviewHistory; rubric: TrialRubric; events: EventEnvelope[]; demo: boolean; onHistory: (history: ReviewHistory) => void }) {
  const last = latestReview(history);
  const fallback = useMemo<ReviewFormDraft>(() => ({
    reviewer_name: last?.reviewer_name ?? "",
    dimensions: rubric.dimensions.map((item) => last?.dimensions.find((dimension) => dimension.dimension_id === item.dimension_id) ?? { dimension_id: item.dimension_id, rating: null, rationale: "当前可见对话不足以判断。", evidence_seq: [] }),
    milestones: rubric.milestones.map((item) => last?.milestones.find((milestone) => milestone.milestone_id === item.milestone_id) ?? { milestone_id: item.milestone_id, status: "insufficient_evidence", rationale: "当前可见对话不足以判断。", evidence_seq: [] }),
    notes: last?.notes ?? "", base_revision: history.latest_revision, pending: null,
  }), [last, rubric, history.latest_revision]);
  const storageKey = reviewDraftKey(sessionId, history.report_hash);
  const draft = useReviewDraft(storageKey, fallback);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const lock = useRef(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const publicEvents = events.filter((event) => event.type === "utterance" && event.visibility === "public");
  const frozen = saving || Boolean(draft.pending);

  function change(next: Partial<ReviewFormDraft>) { writeReviewDraft(storageKey, { ...draft, ...next }); setMessage(""); }
  function dimensionChange(id: DimensionId, next: Partial<DimensionJudgment>) { change({ dimensions: draft.dimensions.map((item) => item.dimension_id === id ? { ...item, ...next } : item) }); }
  function milestoneChange(id: string, next: Partial<MilestoneJudgment>) { change({ milestones: draft.milestones.map((item) => item.milestone_id === id ? { ...item, ...next } : item) }); }

  async function reconcile(requestId?: string): Promise<boolean> {
    const latest = await api.reviews(sessionId);
    if (!mounted.current) return false;
    onHistory(latest);
    const pendingId = requestId ?? readReviewDraft(storageKey)?.pending?.client_review_id;
    const accepted = latest.items.find((item) => item.client_review_id === pendingId);
    if (accepted && pendingId) {
      resolveReviewDraft(storageKey, pendingId);
      setConflict(false);
      setMessage(`修订 ${accepted.revision} 已保存。原始 AI 报告与旧修订保持不变。`);
      return true;
    }
    return false;
  }

  async function checkSaved() {
    if (lock.current) return;
    lock.current = true; setSaving(true); setMessage("");
    try { if (!await reconcile()) setMessage(readReviewDraft(storageKey)?.pending ? "尚未找到这个请求的保存记录，可以按原请求重试保存。草稿仍保留。" : "已重新读取最新复核，请核对历史后更新保存基准。草稿仍保留。"); }
    catch (error) { if (mounted.current) setMessage(errorMessage(error)); }
    finally { lock.current = false; if (mounted.current) setSaving(false); }
  }

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (lock.current) return;
    if (!draft.reviewer_name.trim()) { setMessage("请填写复核者姓名或本机标识。"); return; }
    if (draft.dimensions.some((item) => !item.rationale.trim() || (item.rating !== null && (item.evidence_seq.length === 0 || (!demo && !item.evidence_seq.some((seq) => publicEvents.some((entry) => entry.seq === seq && entry.actor === "trainee"))))))) { setMessage("每项判断都需填写理由；有评级的维度请至少选择一条证据，亲自练习还需包含学员发言。"); return; }
    if (draft.milestones.some((item) => !item.rationale.trim() || (item.status === "observed" && item.evidence_seq.length === 0))) { setMessage("请填写观察目标的理由，并为“观察到”选择对应发言。"); return; }
    if (!draft.pending && draft.base_revision !== history.latest_revision) { setConflict(true); setMessage("当前草稿基于较早的修订，请核对最新历史，再更新保存基准。"); return; }
    const command = draft.pending ?? {
      client_review_id: crypto.randomUUID(), expected_revision: draft.base_revision, report_hash: history.report_hash, rubric_hash: history.rubric_hash,
      reviewer_name: draft.reviewer_name.trim(), dimensions: draft.dimensions, milestones: draft.milestones, notes: draft.notes.trim(),
    } satisfies ReviewSubmission;
    writeReviewDraft(storageKey, { ...draft, pending: command });
    lock.current = true; setSaving(true); setMessage("");
    try {
      const record = await api.saveReview(sessionId, command);
      resolveReviewDraft(storageKey, command.client_review_id);
      if (!mounted.current) return;
      onHistory({ ...history, latest_revision: Math.max(history.latest_revision, record.revision), items: [...history.items.filter((item) => item.review_id !== record.review_id), record] });
      setConflict(false); setMessage(`修订 ${record.revision} 已保存。原始 AI 报告与旧修订保持不变。`);
    } catch (error) {
      if (!mounted.current) return;
      setMessage(errorMessage(error));
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        const current = readReviewDraft(storageKey);
        if (current?.pending?.client_review_id === command.client_review_id) writeReviewDraft(storageKey, { ...current, pending: null });
        setConflict(error.status === 409 && error.code === "review_revision_conflict");
      }
      try { await reconcile(command.client_review_id); } catch { /* Retain the original request for an exact retry. */ }
    } finally { lock.current = false; if (mounted.current) setSaving(false); }
  }

  return <form className="review-form" onSubmit={submit}>
    <p className="evaluation-copy">{last ? `当前已有修订 ${history.latest_revision}。修改后将保存为新的修订，旧记录仍可查看。` : "请先独立查看原发言，再填写观察。表单不自动采用 AI 评级。"}复核者身份由填写者提供，本系统不认证专业资质。</p>
    <fieldset disabled={frozen}><div className="reviewer-field"><label htmlFor="reviewer-name">复核者姓名或本机标识</label><input id="reviewer-name" type="text" maxLength={80} required value={draft.reviewer_name} onChange={(event) => change({ reviewer_name: event.target.value })} /></div>
      {rubric.dimensions.map((dimension) => {
        const item = draft.dimensions.find((entry) => entry.dimension_id === dimension.dimension_id);
        if (!item) return null;
        return <fieldset className="review-dimension" key={dimension.dimension_id} data-review-dimension={dimension.dimension_id}><legend>{dimension.label}</legend><p className="field-help">{dimension.description}</p><div className="review-rating-field"><label htmlFor={`rating-${dimension.dimension_id}`}>人工观察等级</label><select id={`rating-${dimension.dimension_id}`} value={item.rating === null ? "unknown" : item.rating} onChange={(event) => dimensionChange(dimension.dimension_id, { rating: event.target.value === "unknown" ? null : Number(event.target.value) as Rating })}><option value="unknown">证据不足</option><option value="0">0 · 对照本项锚点</option><option value="1">1 · 对照本项锚点</option><option value="2">2 · 对照本项锚点</option></select></div><details className="dimension-anchors"><summary>对照本项观察锚点</summary><ol>{["0", "1", "2"].map((rating) => <li key={rating}>{rating} · {dimension.anchors[rating]}</li>)}</ol></details><EvidencePicker id={`review-evidence-${dimension.dimension_id}`} events={publicEvents} selected={item.evidence_seq} disabled={frozen} onChange={(evidence_seq) => dimensionChange(dimension.dimension_id, { evidence_seq })} /><div className="review-note-field"><label htmlFor={`rationale-${dimension.dimension_id}`}>人工判断理由</label><textarea id={`rationale-${dimension.dimension_id}`} required maxLength={1200} value={item.rationale} onChange={(event) => dimensionChange(dimension.dimension_id, { rationale: event.target.value })} /></div></fieldset>;
      })}
      {rubric.milestones.map((milestone) => {
        const item = draft.milestones.find((entry) => entry.milestone_id === milestone.milestone_id);
        if (!item) return null;
        return <fieldset className="review-dimension" key={milestone.milestone_id} data-review-milestone={milestone.milestone_id}><legend>观察目标 · {milestone.label}</legend><p className="field-help">{milestone.description}</p><div className="review-rating-field"><label htmlFor={`milestone-${milestone.milestone_id}`}>人工观察状态</label><select id={`milestone-${milestone.milestone_id}`} value={item.status} onChange={(event) => milestoneChange(milestone.milestone_id, { status: event.target.value as MilestoneStatus })}>{Object.entries(MILESTONE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></div><EvidencePicker id={`review-milestone-evidence-${milestone.milestone_id}`} events={publicEvents} selected={item.evidence_seq} disabled={frozen} onChange={(evidence_seq) => milestoneChange(milestone.milestone_id, { evidence_seq })} /><div className="review-note-field"><label htmlFor={`milestone-rationale-${milestone.milestone_id}`}>观察目标的理由</label><textarea id={`milestone-rationale-${milestone.milestone_id}`} required maxLength={1200} value={item.rationale} onChange={(event) => milestoneChange(milestone.milestone_id, { rationale: event.target.value })} /></div></fieldset>;
      })}
      <div className="review-note-field"><label htmlFor="review-notes">本次复核备注（可选）</label><textarea id="review-notes" maxLength={2000} value={draft.notes} onChange={(event) => change({ notes: event.target.value })} /></div>
    </fieldset>
    {message && <div className={`review-message ${conflict ? "is-conflict" : ""}`} role="status">{message}</div>}
    {conflict && <div className="review-message is-conflict"><p>草稿仍基于修订 {draft.base_revision}，当前最新为修订 {history.latest_revision}。请先查看下方历史；更新基准不会修改你的草稿。</p><button type="button" className="text-button" disabled={saving || history.latest_revision <= draft.base_revision} onClick={() => { change({ base_revision: history.latest_revision, pending: null }); setConflict(false); setMessage("已更新保存基准。请再次核对，再点击保存。"); }}>已核对历史，更新保存基准</button><button type="button" className="text-button" disabled={saving} onClick={() => { void checkSaved(); }}>重新读取最新修订</button></div>}
    {draft.pending && <div className="review-message"><p>这个修订的保存结果尚未确认。请先核对，或按原请求重试，确认前暂不编辑。</p><div className="button-row"><button type="button" className="button button-small" disabled={saving} onClick={() => { void checkSaved(); }}>核对修订保存结果</button><button type="button" className="button button-small button-primary" disabled={saving} onClick={() => { void submit(); }}>重试保存原修订</button></div></div>}
    <div className="review-form-actions"><button className="button button-primary" type="submit" disabled={frozen || conflict}>{saving ? <><Spinner />正在保存复核</> : `保存为修订 ${draft.base_revision + 1}`}</button><p className="field-help">草稿只保存在当前标签页。人工复核不调用外部模型。保存会追加记录，不改写原 AI 报告。</p></div>
  </form>;
}

export default function ExpertReview({ sessionId, rubric, events, demo, onEvidence }: { sessionId: string; rubric: TrialRubric; events: EventEnvelope[]; demo: boolean; onEvidence: (seq: number) => void }) {
  const [history, setHistory] = useState<ReviewHistory | null>(null);
  const [error, setError] = useState("");
  const mounted = useRef(false);
  const generation = useRef(0);
  const adoptHistory = useCallback((incoming: ReviewHistory) => {
    setHistory((current) => !current || incoming.latest_revision >= current.latest_revision ? incoming : current);
  }, []);
  const load = useCallback(async () => {
    const current = ++generation.current;
    try { const result = await api.reviews(sessionId); if (mounted.current && current === generation.current) { adoptHistory(result); setError(""); } }
    catch (failure) { if (mounted.current && current === generation.current) setError(errorMessage(failure)); }
  }, [sessionId, adoptHistory]);
  useEffect(() => {
    mounted.current = true;
    const current = ++generation.current;
    void api.reviews(sessionId).then((result) => { if (mounted.current && current === generation.current) { adoptHistory(result); setError(""); } }).catch((failure: unknown) => { if (mounted.current && current === generation.current) setError(errorMessage(failure)); });
    return () => { mounted.current = false; generation.current += 1; };
  }, [sessionId, adoptHistory]);
  return <section className="expert-review" aria-labelledby="expert-review-heading"><div className="evaluation-kicker"><div><p className="eyebrow">独立判断，保留修订</p><h3 id="expert-review-heading">人工复核</h3></div><Link className="text-link small-text" href="/calibration">查看校准记录 ↗</Link></div><p className="evaluation-copy">由复核者对照试行量规和实际发言填写。人工复核不会让 AI 内容自动变成已校准结论。{demo ? "自动示范的复核只作观察，不计入学员校准样本。" : ""}</p>{error ? <div className="form-alert" role="alert"><p>{error}</p><button className="text-button" onClick={() => { void load(); }}>重新读取复核</button></div> : history ? <><details className="review-editor-toggle"><summary>填写或修订人工复核 · 当前 {history.latest_revision} 版</summary><ReviewEditor sessionId={sessionId} history={history} rubric={rubric} events={events} demo={demo} onHistory={adoptHistory} /></details><RevisionHistory history={history} rubric={rubric} onEvidence={onEvidence} /></> : <div className="loading-line"><Spinner />正在读取人工复核记录</div>}</section>;
}
