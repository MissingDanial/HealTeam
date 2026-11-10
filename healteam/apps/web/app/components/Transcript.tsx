"use client";

import { useEffect, useRef, useState } from "react";
import type { EventEnvelope, SessionSnapshot } from "../lib/types";
import { ACTOR_LABELS } from "../lib/types";
import { EmptyState } from "./Shared";

export default function Transcript({ events, snapshot }: { events: EventEnvelope[]; snapshot: SessionSnapshot }) {
  const log = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const utterances = events.filter((event) => event.type === "utterance" && event.visibility === "public");

  useEffect(() => {
    if (log.current && stickToBottom.current) log.current.scrollTop = log.current.scrollHeight;
  }, [utterances.length, snapshot.state]);

  function latest() {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
    stickToBottom.current = true;
    setShowLatest(false);
  }

  return <div className="transcript-wrap">
    <div className="transcript" id="conversation-log" ref={log} role="log" aria-label="会话记录" aria-live="polite" aria-relevant="additions" onScroll={() => {
      const element = log.current;
      if (!element) return;
      stickToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
      setShowLatest(!stickToBottom.current);
    }}>
      {!utterances.length && <EmptyState title={snapshot.mode === "training" ? "由你开始这次对话" : "正在准备自动演示"}>{snapshot.mode === "training" ? "写下你的第一次干预，家庭成员会回应你的表达。" : "AI 治疗师会带领对话，你可以在这里观察整个过程。"}</EmptyState>}
      {utterances.map((event, index) => <div key={event.seq} data-event-seq={event.seq}>
        {(index === 0 || utterances[index - 1].round !== event.round) && <div className="turn-divider"><span>第 {event.round} 轮</span></div>}
        <article className={`utterance actor-${event.actor} ${event.source === "human" ? "is-human" : ""}`} data-actor={event.actor} data-source={event.source}>
          <div className="actor-avatar" aria-hidden="true">{({ trainee: "你", doctor: "医", father: "父", mother: "母", patient: "子", observer: "观", psychiatrist: "督", system: "·" } as const)[event.actor]}</div>
          <div className="utterance-body"><div className="utterance-byline"><strong>{ACTOR_LABELS[event.actor]}</strong><span>{event.source === "human" ? "本人发言" : snapshot.model_mode === "mock" ? "预设回应" : "AI 模拟"}</span></div><p>{typeof event.payload.content === "string" ? event.payload.content : ""}</p></div>
        </article>
      </div>)}
      {snapshot.state === "running" && snapshot.phase === "simulation" && <div className="responding-line" role="status"><span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>{snapshot.mode === "training" ? "家庭成员正在回应…" : "演示正在继续…"}</div>}
    </div>
    {showLatest && <button className="latest-button" onClick={latest}>回到最新发言 ↓</button>}
  </div>;
}
