"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, errorMessage, failureMessage, receiptSnapshot } from "../lib/api";
import { resolveSessionAttempt, saveSessionDraft, useSessionDraft } from "../lib/draft-storage";
import type { CaseSummary, CommandReceipt } from "../lib/types";
import { completedRounds, formatTime, isTerminal, SCHOOL_LABELS, sessionLabel } from "../lib/types";
import { useSession } from "../lib/use-session";
import ReportPanel from "./ReportPanel";
import { ModeNotice, SiteHeader, Spinner, StatusBadge } from "./Shared";
import Transcript from "./Transcript";
import UsagePanel from "./UsagePanel";

type PendingAction = "message" | "finish" | "cancel" | "retry" | "sync" | null;

export default function SessionWorkspace({ sessionId, initialTab = "conversation" }: { sessionId: string; initialTab?: "conversation" | "report" }) {
  const session = useSession(sessionId);
  const { snapshot, events, refresh, adoptSnapshot, loadReport } = session;
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [tab, setTab] = useState(initialTab);
  const [evidenceSequence, setEvidenceSequence] = useState<number | null>(null);
  const { ready: draftReady, content: draft, attempt: uncertain } = useSessionDraft(sessionId);
  const [busy, setBusy] = useState<PendingAction>(null);
  const [actionFailure, setActionFailure] = useState<{ message: string; commandId?: string }>({ message: "" });
  const actionError = actionFailure.commandId && events.some((entry) => entry.source === "human" && entry.payload.client_message_id === actionFailure.commandId) ? "" : actionFailure.message;
  const setActionError = (message: string, commandId?: string) => setActionFailure({ message, commandId });
  const actionLock = useRef(false);
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!draftReady) return;
    const accepted = uncertain && events.some((entry) => entry.source === "human" && entry.payload.client_message_id === uncertain.client_message_id);
    if (snapshot && isTerminal(snapshot.state)) saveSessionDraft(sessionId, "", null);
    else if (accepted && uncertain) resolveSessionAttempt(sessionId, uncertain.client_message_id);
  }, [sessionId, draftReady, uncertain, snapshot, events]);

  useEffect(() => {
    let current = true;
    void api.cases().then((items) => { if (current) setCases(items); }).catch(() => undefined);
    return () => { current = false; };
  }, []);

  useEffect(() => {
    if (tab !== "conversation" || evidenceSequence === null) return;
    const target = document.querySelector<HTMLElement>(`[data-event-seq="${evidenceSequence}"]`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.tabIndex = -1;
      target.focus({ preventScroll: true });
    }
  }, [tab, evidenceSequence]);

  function selectTab(next: "conversation" | "report") {
    setTab(next);
    const url = `/sessions/${encodeURIComponent(sessionId)}${next === "report" ? "?tab=report" : ""}`;
    window.history.replaceState(window.history.state, "", url);
    if (next === "report" && snapshot?.report_state === "ready") void loadReport();
  }

  async function synchronize(receipt: CommandReceipt) {
    const next = receiptSnapshot(receipt);
    if (next) adoptSnapshot(next);
    try { await refresh(); } catch { setActionError("操作已接受，正在等待会话重新同步。请勿重复操作。"); }
  }

  async function submitMessage(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const content = uncertain?.content ?? draft.trim();
    if (!snapshot || snapshot.mode !== "training" || (!uncertain && snapshot.state !== "awaiting_input") || !content || actionLock.current || !draftReady) return;
    actionLock.current = true;
    setBusy("message");
    setActionError("");
    // Reuse both the UUID and original turn after an ambiguous network failure.
    // A reconnect may already have advanced the visible round in the meantime.
    const command = uncertain ?? { client_message_id: crypto.randomUUID(), expected_turn: snapshot.current_round + 1, content };
    saveSessionDraft(sessionId, command.content, command);
    try {
      const receipt = await api.message(sessionId, command);
      resolveSessionAttempt(sessionId, command.client_message_id);
      await synchronize(receipt);
    } catch (error) {
      setActionError(errorMessage(error), command.client_message_id);
      // A definite rejection did not accept this command. Keep the draft, but
      // let the next click use the refreshed turn and a new request identity.
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        resolveSessionAttempt(sessionId, command.client_message_id, true);
      }
      try {
        const latest = await refresh();
        const accepted = latest.events.some((entry) => entry.type === "utterance" && entry.source === "human" && entry.payload.client_message_id === command.client_message_id);
        if (accepted && !(error instanceof ApiError && error.status === 409)) {
          resolveSessionAttempt(sessionId, command.client_message_id);
          setActionError("");
        }
      } catch { /* The original command and draft remain available for retry. */ }
    } finally {
      actionLock.current = false;
      setBusy(null);
    }
  }

  async function checkMessage() {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy("sync");
    try {
      const latest = await refresh();
      if (uncertain && !latest.events.some((entry) => entry.source === "human" && entry.payload.client_message_id === uncertain.client_message_id)) setActionError("尚未在已保存记录中找到这条干预。可以使用同一请求重试。");
    } catch (error) { setActionError(errorMessage(error)); }
    finally { actionLock.current = false; setBusy(null); }
  }

  async function act(kind: "finish" | "cancel" | "retry") {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(kind);
    setActionError("");
    try {
      const receipt = kind === "finish" ? await api.finish(sessionId) : kind === "cancel" ? await api.cancel(sessionId) : await api.retryReport(sessionId);
      await synchronize(receipt);
      if (kind === "finish" || kind === "retry") selectTab("report");
    } catch (error) {
      setActionError(errorMessage(error));
      void refresh().catch(() => undefined);
    } finally {
      actionLock.current = false;
      setBusy(null);
    }
  }

  if (!snapshot) return <><SiteHeader /><main id="main-content" className="session-loading paper-panel">{session.loading ? <><Spinner label="正在读取会话" /><h1>正在打开这次会话</h1><p>读取已保存的对话与进度。</p></> : <><h1>暂时无法打开会话</h1><p role="alert">{session.error}</p><div className="button-row"><button className="button" onClick={() => { void refresh().catch(() => undefined); }}>重新读取</button><Link className="button button-primary" href="/">返回首页</Link></div></>}</main></>;

  const title = cases.find((item) => item.case_id === snapshot.case_id)?.title ?? "家庭治疗练习";
  const currentCase = cases.find((item) => item.case_id === snapshot.case_id);
  const terminal = isTerminal(snapshot.state);
  const canCompose = snapshot.mode === "training" && snapshot.state === "awaiting_input";
  const canFinish = canCompose && completedRounds(snapshot) >= 1 && !uncertain;
  const hints = events.filter((event) => event.type === "hint" && event.visibility === "learner" && typeof event.payload.content === "string");
  const retryableReview = (snapshot.state === "failed" || snapshot.state === "interrupted") && snapshot.phase !== "simulation" && snapshot.report_state === "failed" && !snapshot.error_code?.includes("budget");
  const disconnected = session.connection === "reconnecting" || session.connection === "offline" || Boolean(session.error);
  const connectionLabel = terminal ? "记录已保存" : session.connection === "connected" ? "更新已连接" : session.connection === "offline" ? "当前离线" : session.connection === "reconnecting" ? "正在重新连接" : "正在连接更新";

  return <>
    <SiteHeader />
    <main id="main-content" className="session-main">
      <header className="session-heading"><div><p className="eyebrow">{snapshot.mode === "training" ? "亲自练习" : "自动演示"} · {SCHOOL_LABELS[snapshot.school]}</p><h1>{title}</h1><p className="session-subtitle">{snapshot.mode === "training" ? "由你担任治疗师，让一次干预连接下一次回应。" : "由 AI 治疗师带领对话，观察家庭成员之间的回应。"}</p></div><div className="session-heading-status"><StatusBadge snapshot={snapshot} /><span>{snapshot.current_round} / {snapshot.max_rounds} 轮</span></div></header>
      <ModeNotice mode={snapshot.model_mode} provider={snapshot.provider} model={snapshot.model} />
      {!terminal && disconnected && <div className="inline-alert connection-alert" role="status"><span>{session.connection === "offline" ? "当前浏览器离线，恢复连接后会自动同步。" : "连接暂时中断，正在自动重连。"}已提交的干预不会自动重发，草稿保留在当前标签页。</span><button className="text-button" onClick={session.reconnect}>重新连接</button></div>}
      {snapshot.state === "interrupted" && <div className="inline-alert" role="status">服务运行曾中断，已有对话已保留。{retryableReview ? "可以在复盘页签中重试，仅生成复盘，不重复对话。" : "这次会话不会自动续跑，可回到首页开始新练习。"}</div>}
      {snapshot.state === "failed" && <div className="inline-alert" role="alert">{failureMessage(snapshot.error_code)}{retryableReview ? "可以在复盘页签中重试生成。" : "可以回看记录，再开始新的练习。"}</div>}
      {snapshot.state === "cancelled" && <div className="inline-alert neutral-alert" role="status">这次会话已取消，已有对话保留供你回看。</div>}

      <div className="session-layout">
        <section className="paper-panel conversation-panel" aria-label="练习工作区">
          <div className="session-tabs" role="tablist" aria-label="会话视图"><button id="conversation-tab" role="tab" aria-selected={tab === "conversation"} aria-controls="conversation-panel" onClick={() => selectTab("conversation")}>治疗对话<span>{events.filter((event) => event.type === "utterance" && event.visibility === "public").length}</span></button><button id="report-tab" role="tab" aria-selected={tab === "report"} aria-controls="report-panel" onClick={() => selectTab("report")}>{snapshot.model_mode === "live" ? "AI 辅助复盘" : "流程复盘"}{snapshot.report_state === "ready" && <span className="tab-ready">已保存</span>}</button><span className={`connection-status ${disconnected && !terminal ? "is-reconnecting" : ""}`} role="status"><span className="small-dot" />{connectionLabel}</span></div>
          {tab === "conversation" ? <div id="conversation-panel" role="tabpanel" aria-labelledby="conversation-tab">
            <Transcript events={events} snapshot={snapshot} />
            {snapshot.mode === "training" && !terminal ? <form className="composer" onSubmit={submitMessage}>
              <div className="composer-top"><label htmlFor="trainee-message">你的干预</label><span>{canCompose ? `下一次发言 · 第 ${snapshot.current_round + 1} 轮` : sessionLabel(snapshot)}</span></div>
              <textarea id="trainee-message" ref={textarea} value={draft} onChange={(event) => saveSessionDraft(sessionId, event.target.value, uncertain)} placeholder={canCompose ? "写下你想对这个家庭说的话…" : "家庭回应完成后，可以继续发言。"} maxLength={2000} rows={3} disabled={!canCompose || Boolean(busy) || !draftReady || Boolean(uncertain)} aria-describedby="composer-help" onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
              <div className="composer-bottom"><p id="composer-help">{draft.length} / 2000<span>草稿保留于当前标签页 · Ctrl / ⌘ + Enter 发送</span></p><button className="button button-primary" type="submit" disabled={!canCompose || Boolean(busy) || !draft.trim() || !draftReady || Boolean(uncertain)}>{busy === "message" ? <><Spinner />正在保存干预</> : <>发送干预<span aria-hidden="true">↑</span></>}</button></div>
            </form> : <div className="conversation-footnote">{terminal ? "这份对话已保存。刷新页面或从历史会话进入，仍可继续查看。" : "正在自动演示。你可以观察对话，也可以取消本次会话。"}</div>}
          </div> : <div id="report-panel" role="tabpanel" aria-labelledby="report-tab"><ReportPanel snapshot={snapshot} report={session.report} events={events} error={session.reportError} busy={busy === "retry"} onReload={() => { void loadReport(); }} onRetry={() => { void act("retry"); }} onEvidence={(seq) => { setEvidenceSequence(seq); selectTab("conversation"); }} /></div>}
          {uncertain && !terminal && busy !== "message" && <div className="pending-message" role="status"><strong>这条干预的保存结果尚未确认</strong><p>先核对保存结果，或重试同一条干预。确认前暂不编辑新内容。</p><div className="button-row"><button className="button button-small button-outline" disabled={Boolean(busy)} onClick={() => { void checkMessage(); }}>{busy === "sync" ? "正在核对…" : "核对保存结果"}</button><button className="button button-small button-primary" disabled={Boolean(busy)} onClick={() => { void submitMessage(); }}>重试这条干预</button></div></div>}
          {actionError && <div className="action-alert" role="alert">{actionError}</div>}
        </section>

        <aside className="session-sidebar">
          <section className="sidebar-section"><p className="eyebrow">这次练习</p><dl className="session-facts"><div><dt>参与方式</dt><dd>{snapshot.mode === "training" ? "亲自练习" : "自动演示"}</dd></div><div><dt>治疗流派</dt><dd>{SCHOOL_LABELS[snapshot.school]}</dd></div><div><dt>已完成轮次</dt><dd>{completedRounds(snapshot)} / {snapshot.max_rounds}</dd></div><div><dt>开始时间</dt><dd>{formatTime(snapshot.created_at)}</dd></div></dl>{currentCase && <p className="sidebar-case">{currentCase.presenting_problem}</p>}</section>
          <UsagePanel snapshot={snapshot} />
          {snapshot.mode === "training" && !terminal && <section className="sidebar-section finish-section"><h2>留一次复盘</h2><p>完成一轮干预与回应后，可以提前结束这次练习。</p><button className="button button-outline full-width" onClick={() => { void act("finish"); }} disabled={!canFinish || Boolean(busy)}>{busy === "finish" ? <><Spinner />正在结束练习</> : "结束练习并复盘"}</button>{!canFinish && <small>{uncertain ? "请先核对这条干预的保存结果" : snapshot.state === "awaiting_input" ? "至少完成一轮后即可复盘" : "请等待这一轮回应完成"}</small>}</section>}
          {hints.length > 0 && <section className="sidebar-section hint-section"><h2>观察提示</h2><p className="field-help">辅助你留意对话，不作为专业结论。</p><ul>{hints.slice(-4).reverse().map((hint) => <li key={hint.seq}><span>第 {hint.round} 轮</span><p>{hint.payload.content}</p></li>)}</ul></section>}
          <section className="session-save-note"><span className="save-icon" aria-hidden="true">↳</span><p>对话保存在本机。<br />关闭或刷新页面不会取消会话。</p></section>
          {!terminal && <button className="text-button cancel-button" onClick={() => { void act("cancel"); }} disabled={Boolean(busy) || snapshot.state === "cancelling"}>{busy === "cancel" || snapshot.state === "cancelling" ? "正在取消…" : "取消本次会话"}</button>}
          {terminal && <Link className="button button-outline full-width" href="/">开始新的练习 ↗</Link>}
        </aside>
      </div>
      <footer className="site-footer"><span>每次练习，保留一个可以回看的片段。</span><span>模拟与流程复盘 · 非专业能力评分</span></footer>
    </main>
  </>;
}
