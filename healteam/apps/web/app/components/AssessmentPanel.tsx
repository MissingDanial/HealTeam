import type { Assessment, TrialRubric } from "../lib/evaluation-types";
import { MILESTONE_LABELS, ratingLabel } from "../lib/evaluation-types";

export default function AssessmentPanel({ rubric, assessment, demo, onEvidence }: { rubric: TrialRubric; assessment: Assessment | null; demo: boolean; onEvidence: (seq: number) => void }) {
  return <section className="evaluation-section" aria-labelledby="assessment-heading">
    <div className="evaluation-kicker"><div><p className="eyebrow">把观察，放回证据里</p><h3 id="assessment-heading">试行教学量规</h3></div><span className="plain-badge">v{rubric.rubric_version} · 未经校准</span></div>
    <p className="evaluation-copy">{rubric.notice}{demo ? "本次为自动示范，以下仅供观察，不代表学员成绩，也不计入学员校准统计。" : "以下为 AI 建议，需要结合原发言核对；各项独立观察，不相加为总分。"}</p>
    {assessment ? <>
      <p className="evaluation-scope">本次 AI 复盘实际参考 {assessment.reviewed_event_seq.length} / {assessment.total_utterances} 条公开发言。{assessment.reviewed_event_seq.length < assessment.total_utterances ? "较早的部分对话未纳入本次判断，可以返回完整对话查看。" : "引用可跳回原发言查看。"}</p>
      <ol className="evaluation-dimensions">{rubric.dimensions.map((dimension, index) => {
        const judgment = assessment.dimensions.find((item) => item.dimension_id === dimension.dimension_id);
        return <li className="evaluation-dimension" key={dimension.dimension_id} data-dimension={dimension.dimension_id}>
          <div className="dimension-heading"><h4><span className="dimension-number">0{index + 1}</span>{dimension.label}</h4><span className={`dimension-rating ${judgment?.rating == null ? "is-unknown" : ""}`}>AI 建议 · {ratingLabel(judgment?.rating ?? null)}</span></div>
          <p className="dimension-rationale">{judgment?.rationale ?? "本项没有可用的 AI 判断。"}</p>
          <div className="review-evidence">{judgment?.evidence_seq.map((seq) => <button className="text-button" key={seq} onClick={() => onEvidence(seq)}>查看对话 #{seq} ↗</button>)}</div>
          <details className="dimension-anchors"><summary>查看观察锚点</summary><p>{dimension.description}</p><ol>{["0", "1", "2"].map((rating) => <li key={rating}><strong>{rating}</strong> · {dimension.anchors[rating]}</li>)}</ol></details>
        </li>;
      })}</ol>
      {rubric.milestones.length > 0 && <div className="evaluation-milestones"><h4>病例观察目标</h4><p className="evaluation-copy">{rubric.milestone_rule}</p>{rubric.milestones.map((milestone) => {
        const judgment = assessment.milestones.find((item) => item.milestone_id === milestone.milestone_id);
        return <div className="evaluation-milestone" key={milestone.milestone_id}><strong>{milestone.label}</strong><span>{MILESTONE_LABELS[judgment?.status ?? "insufficient_evidence"]}</span><p className="dimension-rationale">{judgment?.rationale ?? "本项没有可用判断。"}</p><div className="review-evidence">{judgment?.evidence_seq.map((seq) => <button className="text-button" key={seq} onClick={() => onEvidence(seq)}>查看对话 #{seq} ↗</button>)}</div></div>;
      })}</div>}
    </> : <p className="evaluation-scope">本报告未生成 AI 量规判断。可以依据已保存的原发言填写人工复核；不会生成模型分数或补造结论。</p>}
    <p className="evaluation-copy">{rubric.missing_evidence_rule.replace(/\bnull\b/g, "“证据不足”")}</p>
  </section>;
}
