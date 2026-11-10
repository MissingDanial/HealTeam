import type { SessionSnapshot } from "../lib/types";
import { providerLabel } from "../lib/types";

const number = (value: number) => new Intl.NumberFormat("zh-CN").format(value);

export default function UsagePanel({ snapshot }: { snapshot: SessionSnapshot }) {
  if (snapshot.model_mode !== "live") return null;
  const usage = snapshot.usage;
  return <section className="sidebar-section usage-panel" aria-label="模型用量">
    <p className="eyebrow">模型用量</p>
    {snapshot.provider && <p className="usage-model">{providerLabel(snapshot.provider)}{snapshot.model ? ` · ${snapshot.model}` : ""}</p>}
    {usage ? <>
      <dl className="session-facts usage-facts">
        <div><dt>调用次数</dt><dd>{number(usage.requests)} / {number(usage.max_calls)}</dd></div>
        <div><dt>已报告输入 token</dt><dd>{number(usage.input_tokens)}</dd></div>
        <div><dt>已报告输出 token</dt><dd>{number(usage.output_tokens)}</dd></div>
        <div><dt>预算预留</dt><dd>{number(usage.reserved_tokens)} / {number(usage.token_budget)}</dd></div>
        {usage.unknown_requests > 0 && <div><dt>用量未确认的调用</dt><dd>{number(usage.unknown_requests)}</dd></div>}
      </dl>
      <p className="usage-note">用量仅供回看，不是费用账单。{usage.unknown_requests > 0 ? "部分调用未返回完整用量，预算仍保留。" : "实际费用以模型服务的账单为准。"}</p>
    </> : <p className="usage-note">正在读取已保存的调用用量。</p>}
  </section>;
}
