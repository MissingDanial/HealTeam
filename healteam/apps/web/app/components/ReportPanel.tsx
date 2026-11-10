import type { EventEnvelope, SessionReport, SessionSnapshot } from "../lib/types";
import { formatTime } from "../lib/types";
import AssessmentPanel from "./AssessmentPanel";
import ExpertReview from "./ExpertReview";
import { EmptyState, Spinner } from "./Shared";

function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => part.startsWith("**") && part.endsWith("**") ? <strong key={index}>{part.slice(2, -2)}</strong> : part);
}

function ReportDocument({ content }: { content: string }) {
  const lines = content.split("\n");
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  const flush = (key: number) => {
    if (list.length) { blocks.push(<ul key={`list-${key}`}>{list.map((item, index) => <li key={index}>{inline(item)}</li>)}</ul>); list = []; }
  };
  lines.forEach((line, index) => {
    const match = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)$/);
    if (match) { list.push(match[1]); return; }
    flush(index);
    if (!line.trim()) return;
    if (/^#{1,2}\s/.test(line)) blocks.push(<h3 key={index}>{inline(line.replace(/^#+\s*/, ""))}</h3>);
    else if (/^#{3,6}\s/.test(line)) blocks.push(<h4 key={index}>{inline(line.replace(/^#+\s*/, ""))}</h4>);
    else if (/^>\s?/.test(line)) blocks.push(<blockquote key={index}>{inline(line.replace(/^>\s?/, ""))}</blockquote>);
    else if (/^[-*_]{3,}$/.test(line.trim())) blocks.push(<hr key={index} />);
    else blocks.push(<p key={index}>{inline(line)}</p>);
  });
  flush(lines.length);
  return <div className="report-document">{blocks}</div>;
}

function ReviewDocument({ review, onEvidence }: { review: NonNullable<SessionReport["structured"]>; onEvidence: (seq: number) => void }) {
  function points(items: { text: string; evidence_seq: number[] }[]) {
    return <ul className="review-points">{items.map((item, index) => <li key={index}><p>{item.text}</p><div className="review-evidence" aria-label="对应对话">{item.evidence_seq.map((seq) => <button className="text-button" key={seq} onClick={() => onEvidence(seq)}>查看对话 #{seq} ↗</button>)}</div></li>)}</ul>;
  }
  return <div className="report-document ai-review-document"><h3>这次对话的回看</h3><p>{review.summary}</p><h3>可观察的互动</h3>{points(review.observations)}<h3>下次可以练习</h3>{points(review.next_steps)}</div>;
}

export default function ReportPanel({ snapshot, report, events, error, busy, onReload, onRetry, onEvidence }: { snapshot: SessionSnapshot; report: SessionReport | null; events: EventEnvelope[]; error: string; busy: boolean; onReload: () => void; onRetry: () => void; onEvidence: (seq: number) => void }) {
  const live = snapshot.model_mode === "live";
  const budgetExhausted = Boolean(snapshot.error_code?.includes("budget"));
  const retryable = (snapshot.state === "failed" || snapshot.state === "interrupted") && snapshot.phase !== "simulation" && snapshot.report_state === "failed" && !budgetExhausted;
  const retryButton = <button className="button button-primary" disabled={busy} onClick={onRetry}>{busy ? <><Spinner />正在重试</> : "重试生成复盘"}</button>;
  let body: React.ReactNode;
  if (snapshot.state === "interrupted" && !retryable) {
    body = <EmptyState title="本次会话已中断">已有对话已保留。这次会话不会自动继续，可以回到首页开始新练习。</EmptyState>;
  } else if (snapshot.state === "cancelled") {
    body = <EmptyState title="本次会话已取消">已有对话仍可查看；取消的会话不会继续生成报告。</EmptyState>;
  } else if (error) {
    body = <div className="form-alert" role="alert"><p>{error}</p><button className="text-button" onClick={onReload}>重新读取报告</button>{retryable && <div className="button-row">{retryButton}</div>}</div>;
  } else if (report?.status === "ready" && (report.content || report.structured)) {
    body = <><div className="report-saved"><span className="small-dot" />已保存，可以随时从历史会话重新打开<time dateTime={report.generated_at ?? undefined}>{formatTime(report.generated_at)}</time></div>{report.kind === "ai_review" && report.structured ? <ReviewDocument review={report.structured} onEvidence={onEvidence} /> : <ReportDocument content={report.content ?? ""} />}</>;
  } else if (retryable) {
    body = <div className="report-failed"><h3>复盘尚未完成</h3><p>已有对话已保留，可以从已保存的进度重新生成复盘。重试只处理复盘，不会重复家庭对话。{live ? "重试会再次调用模型服务，可能产生费用。" : ""}</p>{retryButton}</div>;
  } else if (budgetExhausted) {
    body = <EmptyState title="这次会话的模型预算已用完">已有对话仍可查看。本次会话不会继续调用模型，也不会自动重试复盘。</EmptyState>;
  } else if (snapshot.state === "failed" || snapshot.report_state === "failed") {
    body = <EmptyState title="本次会话未能完成">已有对话已保留，可以回看记录，再从首页开始新的练习。</EmptyState>;
  } else if (snapshot.report_state === "generating" || (snapshot.state === "running" && snapshot.phase !== "simulation") || snapshot.report_state === "ready") {
    body = <div className="report-progress" role="status"><Spinner /><h3>{snapshot.report_state === "ready" ? "正在读取已保存的报告" : "正在整理这次练习"}</h3><p>你可以留在这里，也可以稍后从历史会话回来查看。</p></div>;
  } else {
    body = <EmptyState title="对话之后，留一次复盘">至少完成一轮干预与回应，再选择“结束练习并复盘”。达到最大轮次后也会自动生成。</EmptyState>;
  }
  return <div className="report-panel">
    <div className="report-heading"><div><p className="eyebrow">回到这次对话</p><h2>{live ? "AI 辅助复盘" : "流程复盘"}</h2></div><span className="plain-badge">{live ? "未经专家校准" : "非专业评分"}</span></div>
    <p className="report-intro">{live ? "这份 AI 生成的回看基于已保存的对话，相关观察可以跳转到原发言核对。内容未经专家校准，不用于判断专业胜任力或作出临床结论。" : "这份记录帮助你回看干预与家庭回应，核对练习流程。它不用于判断专业胜任力或作出临床结论。"}{snapshot.mode === "demo" ? "自动示范的复盘不代表学员成绩。" : ""}</p>
    {body}
    {report?.status === "ready" && report.rubric && <><AssessmentPanel rubric={report.rubric} assessment={report.assessment ?? null} demo={snapshot.mode === "demo"} onEvidence={onEvidence} /><ExpertReview key={snapshot.session_id} sessionId={snapshot.session_id} rubric={report.rubric} events={events} demo={snapshot.mode === "demo"} onEvidence={onEvidence} /></>}
  </div>;
}
