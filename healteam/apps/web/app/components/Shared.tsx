import Link from "next/link";
import type { ModelMode, SessionSnapshot } from "../lib/types";
import { providerLabel, sessionLabel } from "../lib/types";

export function SiteHeader({ home = false }: { home?: boolean }) {
  return <header className="site-header">
    <Link className="brand" href="/" aria-label="HealTeam 首页">
      <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
      <span><strong>HealTeam</strong><small>家庭治疗练习室</small></span>
    </Link>
    <nav className="header-actions" aria-label="工作区导航"><Link className="text-link" href="/calibration">复核与校准</Link>{home ? <span className="workspace-label"><span className="small-dot" />本机工作区</span> : <Link className="text-link back-link" href="/">← 返回首页</Link>}</nav>
  </header>;
}

export function ModeNotice({ mode, checking = false, provider, model }: { mode?: ModelMode; checking?: boolean; provider?: string | null; model?: string | null }) {
  return <aside className={`mode-notice ${mode === "live" ? "is-live" : ""}`} aria-label="运行模式">
    <span className="notice-symbol" aria-hidden="true">◇</span>
    <div>
      <strong>{mode === "mock" ? "离线流程演示" : mode === "live" ? "真实模型练习 · AI 辅助复盘" : checking ? "正在确认运行模式" : "暂时无法确认运行模式"}</strong>
      <p>{mode === "mock" ? "当前使用预设回复，帮助熟悉练习流程。复盘只核对流程，不提供专业能力评分。" : mode === "live" ? "开始练习后，病例与对话将发送到已配置的模型服务，可能产生费用。AI 复盘未经专家校准，不提供专业能力评分。" : "连接本地服务后，即可确认本次练习使用的回应方式。"}</p>
      {mode === "live" && (provider || model) && <p className="model-identity">{providerLabel(provider)}{provider && model ? " · " : ""}{model}</p>}
    </div>
    {mode && <span className="notice-tag">{mode === "mock" ? "MOCK · 非专业评分" : "LIVE · 未经校准"}</span>}
  </aside>;
}

export function StatusBadge({ snapshot }: { snapshot: SessionSnapshot }) {
  return <span className={`status-badge state-${snapshot.state}`}><span className="small-dot" />{sessionLabel(snapshot)}</span>;
}

export function Spinner({ label = "正在处理" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function EmptyState({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="empty-state"><span className="empty-mark" aria-hidden="true">○</span><h3>{title}</h3><p>{children}</p></div>;
}
