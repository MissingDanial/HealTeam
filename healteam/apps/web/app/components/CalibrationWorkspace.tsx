"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../lib/api";
import type { CalibrationSummary } from "../lib/evaluation-types";
import { SCHOOL_LABELS, providerLabel } from "../lib/types";
import { EmptyState, SiteHeader, Spinner } from "./Shared";

function percent(value: number | null) { return value === null ? "—" : `${(value * 100).toFixed(1)}%`; }

export default function CalibrationWorkspace() {
  const [summary, setSummary] = useState<CalibrationSummary | null>(null);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const generation = useRef(0);
  const mounted = useRef(false);
  const load = useCallback(async () => {
    const current = ++generation.current;
    try { const result = await api.calibration(); if (mounted.current && current === generation.current) { setSummary(result); setError(""); } }
    catch (failure) { if (mounted.current && current === generation.current) setError(errorMessage(failure)); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    const current = ++generation.current;
    void api.calibration().then((result) => { if (mounted.current && current === generation.current) { setSummary(result); setError(""); } }).catch((failure: unknown) => { if (mounted.current && current === generation.current) setError(errorMessage(failure)); });
    return () => { mounted.current = false; generation.current += 1; };
  }, []);

  async function exportData() {
    if (exporting) return;
    setExporting(true); setExportMessage("");
    try {
      const body = await api.calibrationExport();
      if (!mounted.current) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], { type: "application/json;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "healteam-calibration.json";
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setExportMessage("已导出去标识化的量规对照 JSON，不包含复核者姓名、备注或原对话文本。");
    } catch (failure) { if (mounted.current) setExportMessage(errorMessage(failure)); }
    finally { if (mounted.current) setExporting(false); }
  }

  return <><SiteHeader /><main id="main-content" className="calibration-main"><header className="calibration-heading"><div><p className="eyebrow">让建议接受复核</p><h1>复核与校准记录</h1><p>把 AI 建议与人工观察逐项对照。这里只描述本机已有样本的一致情况；试行量规尚待专家校准，不代表临床有效性、专业胜任力或认证结果。</p></div><div className="calibration-actions"><button className="button button-outline" onClick={() => { void load(); }}>刷新记录</button><button className="button button-primary" disabled={!summary || exporting} onClick={() => { void exportData(); }}>{exporting ? <><Spinner />正在导出</> : "导出对照 JSON"}</button></div></header>
    {error ? <div className="form-alert" role="alert"><p>{error}</p><button className="text-button" onClick={() => { void load(); }}>重新读取校准记录</button></div> : !summary ? <div className="paper-panel loading-line"><Spinner />正在读取复核统计</div> : <>
      <dl className="calibration-metrics"><div className="calibration-metric"><dt>已复核的校准样本</dt><dd>{summary.reviewed_sessions}</dd><small>可用于校准 {summary.eligible_sessions} 次</small></div><div className="calibration-metric"><dt>有效维度配对</dt><dd>{summary.paired_dimensions}</dd><small>AI 与人工均有非空等级</small></div><div className="calibration-metric"><dt>精确一致率</dt><dd>{percent(summary.exact_agreement)}</dd><small>{summary.exact_matches} / {summary.paired_dimensions} 项等级完全相同</small></div><div className="calibration-metric"><dt>不足以配对</dt><dd>{summary.insufficient_pairs}</dd><small>不把证据不足当作一致或不一致</small></div></dl>
      <p className="evaluation-scope">每份报告仅使用最新人工修订，重复修改不会增加样本数。另有 {summary.out_of_window_pairs} 项人工证据超出 AI 参考范围，未计入有效配对。自动示范不计入学员校准（已排除 {summary.excluded_demo_sessions} 次）。{summary.paired_dimensions === 0 ? "目前没有可计算一致率的有效配对。" : "当前数字仅描述已有样本，样本量不代表已经完成专家校准。"}</p>
      <section className="paper-panel calibration-panel"><p className="eyebrow">分开比较同一套条件</p><h2>量规与模型分组</h2><p>不同量规版本、流派和模型分别列出；人工身份由填写者提供，不视为已验证的专家资质。离线人工复核可保存，但没有 AI 等级时不产生有效配对。</p>{summary.groups.length ? <div className="calibration-table-wrap"><table className="calibration-table"><thead><tr><th scope="col">量规 / 流派 / 模型</th><th scope="col">复核会话</th><th scope="col">有效配对</th><th scope="col">完全一致</th><th scope="col">不足配对</th><th scope="col">范围不同</th></tr></thead><tbody>{summary.groups.map((group) => <tr key={`${group.rubric_hash}:${group.school}:${group.provider}:${group.model}`}><td>v{group.rubric_version} · {SCHOOL_LABELS[group.school]}<br /><small className="muted">{group.provider ? providerLabel(group.provider) : "离线模板"}{group.model ? ` · ${group.model}` : ""}</small></td><td>{group.reviewed_sessions}</td><td>{group.paired_dimensions}</td><td>{percent(group.exact_agreement)}<br /><small className="muted">{group.exact_matches} / {group.paired_dimensions}</small></td><td>{group.insufficient_pairs}</td><td>{group.out_of_window_pairs}</td></tr>)}</tbody></table></div> : <EmptyState title="还没有可对照的复核记录">完成带试行量规的训练后，在复盘页面填写人工复核。保存后，这里会按相同条件展示统计。</EmptyState>}</section>
      <section className="paper-panel calibration-panel"><h2>对照数据留在你手中</h2><p>点击导出时下载量规版本、模型分组、AI 与人工等级和证据序号。导出文件不包含复核者姓名、自由备注、原对话文本、病例私有背景或模型凭据。它用于后续人工分析，不构成已校准数据集认证。</p>{exportMessage && <div className="review-message" role="status">{exportMessage}</div>}</section>
    </>}
    <footer className="site-footer"><span>保留证据，也保留判断的边界。</span><span>试行量规 · 等待专家校准</span></footer>
  </main></>;
}
