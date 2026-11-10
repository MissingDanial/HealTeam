"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, errorMessage } from "../lib/api";
import { readCreateDraft, resolveCreateAttempt, saveCreateDraft, useCreateDraft } from "../lib/draft-storage";
import type { CaseSummary, HealthStatus, School, SessionHistory, SessionMode } from "../lib/types";
import { formatTime, isTerminal, SCHOOL_LABELS, SCHOOLS } from "../lib/types";
import { EmptyState, ModeNotice, SiteHeader, Spinner, StatusBadge } from "./Shared";

const PAGE_SIZE = 8;
type Setup = { caseId: string; school: School; mode: SessionMode; rounds: string };

export default function HomeWorkspace() {
  const router = useRouter();
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const savedCreate = useCreateDraft();
  const [setup, setSetup] = useState<Setup | null>(null);
  const selection = setup ?? { caseId: savedCreate?.case_id ?? "", school: savedCreate?.school ?? "structural", mode: savedCreate?.mode ?? "training", rounds: String(savedCreate?.max_rounds ?? 10) };
  const caseId = cases.some((item) => item.case_id === selection.caseId) ? selection.caseId : (cases[0]?.case_id ?? "");
  const { school, mode, rounds } = selection;
  function configure(change: Partial<Setup>) { setSetup({ ...selection, ...change }); }
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [initialError, setInitialError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [history, setHistory] = useState<SessionHistory>({ items: [], total: 0 });
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const createAttempt = useRef<{ fingerprint: string; id: string } | null>(null);
  const createBusy = useRef(false);
  const mounted = useRef(false);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => {
    let current = true;
    void Promise.allSettled([api.cases(), api.readiness()]).then(([caseResult, healthResult]) => {
      if (!current) return;
      if (caseResult.status === "fulfilled") {
        setCases(caseResult.value);
      }
      setHealth(healthResult.status === "fulfilled" ? healthResult.value : null);
      setInitialError(caseResult.status === "rejected" || healthResult.status === "rejected" ? "暂时无法读取本地练习服务，请确认服务已启动后重试。" : "");
      setInitialLoading(false);
    });
    return () => { current = false; };
  }, [refreshKey]);

  useEffect(() => {
    let current = true;
    void api.history(PAGE_SIZE, offset).then((data) => {
      if (current) { setHistory(data); setHistoryError(""); setHistoryLoading(false); }
    }).catch(() => {
      if (current) { setHistoryError("暂时无法读取历史会话。"); setHistoryLoading(false); }
    });
    return () => { current = false; };
  }, [offset, refreshKey]);

  const selectedCase = cases.find((item) => item.case_id === caseId);
  const roundCount = Number(rounds);
  const validRounds = rounds.trim() !== "" && Number.isInteger(roundCount) && roundCount >= 1 && roundCount <= 30;

  async function createSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (createBusy.current || !selectedCase || !validRounds || !health?.model_mode) return;
    createBusy.current = true;
    setCreating(true);
    setCreateError("");
    setActiveSession(null);
    const config = { case_id: caseId, school, mode, max_rounds: roundCount };
    const fingerprint = JSON.stringify(config);
    if (createAttempt.current?.fingerprint !== fingerprint) {
      const saved = readCreateDraft();
      const savedConfig = saved ? { case_id: saved.case_id, school: saved.school, mode: saved.mode, max_rounds: saved.max_rounds } : null;
      createAttempt.current = { fingerprint, id: saved && JSON.stringify(savedConfig) === fingerprint ? saved.client_request_id : crypto.randomUUID() };
    }
    const commandId = createAttempt.current.id;
    saveCreateDraft({ ...config, client_request_id: commandId });
    try {
      const snapshot = await api.create({ ...config, client_request_id: commandId });
      resolveCreateAttempt(commandId);
      if (mounted.current) router.push(`/sessions/${encodeURIComponent(snapshot.session_id)}`);
    } catch (error) {
      if (!mounted.current) return;
      setCreateError(errorMessage(error));
      if (error instanceof ApiError && error.code === "active_session_exists" && error.sessionId) setActiveSession(error.sessionId);
      setCreating(false);
      createBusy.current = false;
    }
  }

  function retryLoading() {
    setInitialLoading(true);
    setHistoryLoading(true);
    setRefreshKey((value) => value + 1);
  }

  function changePage(nextOffset: number) {
    setHistoryLoading(true);
    setOffset(nextOffset);
  }

  return <>
    <SiteHeader home />
    <main id="main-content" className="home-main">
      <section className="home-intro">
        <div><p className="eyebrow">留一点时间，练习如何倾听</p><h1>在家庭的对话里，<br />练习你的下一步。</h1><p className="intro-copy">从一个案例开始，由你担任治疗师。<br className="desktop-break" />尝试一次干预，观察回应，再回到对话中复盘。</p></div>
        <ol className="journey"><li><span>01</span><div><strong>选择一个情境</strong><p>同一家庭，不同流派的视角</p></div></li><li><span>02</span><div><strong>写下你的干预</strong><p>由家庭成员回应你的表达</p></div></li><li><span>03</span><div><strong>留下一次复盘</strong><p>保存对话，随时回来查看</p></div></li></ol>
      </section>

      <ModeNotice mode={health?.model_mode} checking={initialLoading} provider={health?.provider} model={health?.model} />
      {initialError && <div className="inline-alert" role="alert"><span>{initialError}</span><button className="text-button" onClick={retryLoading}>重新连接</button></div>}

      <section className="setup-layout" aria-labelledby="setup-heading">
        <form className="paper-panel setup-form" onSubmit={createSession}>
          <div className="section-heading"><div><p className="eyebrow">新的会话</p><h2 id="setup-heading">准备这次练习</h2></div><span className="section-number" aria-hidden="true">01—03</span></div>
          <fieldset disabled={creating || initialLoading}>
            <label className="field-label" htmlFor="case-select">练习案例</label>
            <select id="case-select" value={caseId} onChange={(event) => configure({ caseId: event.target.value })} required>
              {!cases.length && <option value="">{initialLoading ? "正在读取案例…" : "暂无可用案例"}</option>}
              {cases.map((item) => <option key={item.case_id} value={item.case_id}>{item.title}</option>)}
            </select>

            <fieldset className="choice-fieldset"><legend className="field-label">治疗流派</legend><div className="school-options">
              {SCHOOLS.map((item) => <label className={`choice-card ${school === item.value ? "is-selected" : ""}`} key={item.value}>
                <input type="radio" name="school" value={item.value} checked={school === item.value} onChange={() => configure({ school: item.value })} />
                <span className="radio-indicator" aria-hidden="true" /><strong>{item.label}</strong><small>{item.subtitle}</small><span className="choice-description">{item.description}</span>
              </label>)}
            </div></fieldset>

            <fieldset className="choice-fieldset"><legend className="field-label">参与方式</legend><div className="mode-options">
              <label className={`choice-card mode-choice ${mode === "training" ? "is-selected" : ""}`}><input type="radio" name="mode" value="training" checked={mode === "training"} onChange={() => configure({ mode: "training" })} /><span className="radio-indicator" aria-hidden="true" /><strong>亲自练习</strong><span className="choice-description">你来担任治疗师，亲自发出每一次干预。</span></label>
              <label className={`choice-card mode-choice ${mode === "demo" ? "is-selected" : ""}`}><input type="radio" name="mode" value="demo" checked={mode === "demo"} onChange={() => configure({ mode: "demo" })} /><span className="radio-indicator" aria-hidden="true" /><strong>自动演示</strong><span className="choice-description">由 AI 治疗师带领对话，你来观察过程。</span></label>
            </div></fieldset>

            <div className="rounds-field"><div><label className="field-label" htmlFor="rounds-limit">最大轮次</label><p id="rounds-help" className="field-help">每轮包含一次治疗师干预与对应的家庭回应。</p></div><div className="number-field"><input id="rounds-limit" type="number" min="1" max="30" step="1" required value={rounds} onChange={(event) => configure({ rounds: event.target.value })} aria-describedby="rounds-help" /><span>轮</span></div></div>
          </fieldset>
          {createError && <div className="form-alert" role="alert"><p>{createError}</p>{activeSession && <Link className="text-link" href={`/sessions/${encodeURIComponent(activeSession)}`}>继续已有会话 →</Link>}</div>}
          <div className="setup-bottom"><p>{health?.model_mode === "live" ? "生成回应与复盘会调用模型服务。完成一轮后可提前结束。" : "完成一轮后，也可以提前结束并复盘。"}</p><button className="button button-primary" type="submit" disabled={creating || initialLoading || !selectedCase || !validRounds || !health?.model_mode}>{creating ? <><Spinner />正在准备会话</> : <>{mode === "training" ? "开始亲自练习" : "开始自动演示"}<span aria-hidden="true">↗</span></>}</button></div>
        </form>

        <aside className="case-preview"><p className="eyebrow">本次练习情境</p><div className="case-illustration" aria-hidden="true"><span /><span /><span /><i /><i /></div><h3>{selectedCase?.title ?? "从一个家庭故事开始"}</h3><p>{selectedCase?.presenting_problem ?? "选择案例后，可以在这里查看本次练习的情境。"}</p><div className="preview-divider" /><p className="quiet-note">不必急着寻找一个完美的答案。<br />先观察对话怎样发生，再思考你的表达带来了什么。</p><span className="preview-footnote">对话仅用于模拟与学习</span></aside>
      </section>

      <section className="history-section" aria-labelledby="history-heading"><div className="section-heading"><div><p className="eyebrow">保留每一步</p><h2 id="history-heading">最近的会话</h2></div><span className="muted small-text">保存于本机</span></div>
        <div className="paper-panel history-panel" aria-busy={historyLoading}>
          {historyLoading ? <div className="loading-line"><Spinner />正在读取历史会话</div> : historyError ? <div className="inline-alert"><span>{historyError}</span><button className="text-button" onClick={retryLoading}>重新读取</button></div> : !history.items.length ? <EmptyState title="还没有开始的会话">完成的练习和中断的对话，都会保留在这里。</EmptyState> : <ul className="history-list">
            {history.items.map((item) => <li key={item.session_id} data-session-id={item.session_id}><Link className="history-row" href={`/sessions/${encodeURIComponent(item.session_id)}${item.report_state === "ready" ? "?tab=report" : ""}`}><div className="history-title"><strong>{cases.find((entry) => entry.case_id === item.case_id)?.title ?? "家庭治疗会话"}</strong><span>{SCHOOL_LABELS[item.school]}<i>·</i>{item.mode === "training" ? "亲自练习" : "自动演示"}<i>·</i>{item.current_round} / {item.max_rounds} 轮</span></div><StatusBadge snapshot={item} /><time dateTime={item.updated_at ?? item.created_at}>{formatTime(item.updated_at ?? item.created_at)}</time><span className="history-action">{item.report_state === "ready" ? "查看复盘" : isTerminal(item.state) ? "查看记录" : "继续会话"}<span aria-hidden="true">↗</span></span></Link></li>)}
          </ul>}
        </div>
        {history.total > PAGE_SIZE && <nav className="pagination" aria-label="会话历史分页"><span>{offset + 1}—{Math.min(offset + PAGE_SIZE, history.total)} / 共 {history.total} 次</span><div><button className="button button-small" onClick={() => changePage(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0 || historyLoading}>上一页</button><button className="button button-small" onClick={() => changePage(offset + PAGE_SIZE)} disabled={offset + PAGE_SIZE >= history.total || historyLoading}>下一页</button></div></nav>}
      </section>
      <footer className="site-footer"><span>HealTeam · 练习、观察，再回到对话</span><span>模拟内容需要结合专业教学判断。</span></footer>
    </main>
  </>;
}
