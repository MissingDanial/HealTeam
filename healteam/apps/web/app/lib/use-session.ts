"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import type { EventEnvelope, SessionDetail, SessionReport, SessionSnapshot } from "./types";
import { isTerminal } from "./types";

type Connection = "connecting" | "connected" | "reconnecting" | "offline" | "idle";
type State = { snapshot: SessionSnapshot | null; events: EventEnvelope[]; loading: boolean; error: string; connection: Connection; report: SessionReport | null; reportError: string };
type Action = { type: "snapshot"; detail: SessionDetail | SessionSnapshot } | { type: "event"; event: EventEnvelope } | { type: "load_error"; message: string } | { type: "connection"; connection: Connection } | { type: "report"; report: SessionReport } | { type: "report_error"; message: string };

function mergeEvents(previous: EventEnvelope[], incoming: EventEnvelope[]): EventEnvelope[] {
  const bySequence = new Map(previous.map((event) => [event.seq, event]));
  for (const event of incoming) bySequence.set(event.seq, event);
  return [...bySequence.values()].sort((left, right) => left.seq - right.seq);
}

function reducer(state: State, action: Action): State {
  if (action.type === "snapshot") {
    const next = action.detail;
    const snapshot = !state.snapshot || next.last_event_seq >= state.snapshot.last_event_seq ? next : state.snapshot;
    const incoming = "events" in next ? next.events : [];
    return { ...state, snapshot, events: mergeEvents(state.events, incoming), loading: false, error: "" };
  }
  if (action.type === "event") {
    const event = action.event;
    if (state.events.some((entry) => entry.seq === event.seq)) return state;
    const candidate = event.payload.snapshot;
    let snapshot = state.snapshot;
    if (candidate && (!snapshot || event.seq >= snapshot.last_event_seq)) snapshot = { ...snapshot, ...candidate };
    if (snapshot) snapshot = { ...snapshot, last_event_seq: Math.max(snapshot.last_event_seq, event.seq) };
    return { ...state, snapshot, events: mergeEvents(state.events, [event]) };
  }
  if (action.type === "load_error") return { ...state, loading: false, error: action.message };
  if (action.type === "connection") return { ...state, connection: action.connection };
  if (action.type === "report") return { ...state, report: action.report, reportError: "" };
  return { ...state, reportError: action.message };
}

function envelope(value: unknown, sessionId: string): value is EventEnvelope {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<EventEnvelope>;
  if (event.session_id !== sessionId || !Number.isInteger(event.seq) || Number(event.seq) <= 0 || !["status_changed", "utterance", "hint", "report_ready", "error"].includes(String(event.type)) || !["public", "learner"].includes(String(event.visibility)) || typeof event.payload !== "object" || event.payload === null) return false;
  const snapshot = event.payload.snapshot;
  return !snapshot || (snapshot.session_id === sessionId && Number.isInteger(snapshot.last_event_seq) && ["created", "awaiting_input", "running", "cancelling", "completed", "cancelled", "failed", "interrupted"].includes(snapshot.state));
}

const INITIAL: State = { snapshot: null, events: [], loading: true, error: "", connection: "connecting", report: null, reportError: "" };

export function useSession(sessionId: string) {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const lastSequence = useRef(0);
  const mounted = useRef(false);
  const snapshotRequest = useRef(0);
  const reportRequest = useRef(0);
  const [connectionEpoch, setConnectionEpoch] = useState(0);

  const adoptSnapshot = useCallback((detail: SessionDetail | SessionSnapshot) => {
    if (!mounted.current) return;
    lastSequence.current = Math.max(lastSequence.current, detail.last_event_seq);
    dispatch({ type: "snapshot", detail });
  }, []);

  const refresh = useCallback(async () => {
    const request = ++snapshotRequest.current;
    try {
      const detail = await api.session(sessionId);
      if (request === snapshotRequest.current) adoptSnapshot(detail);
      return detail;
    } catch (error) {
      if (mounted.current && request === snapshotRequest.current) dispatch({ type: "load_error", message: errorMessage(error) });
      throw error;
    }
  }, [sessionId, adoptSnapshot]);

  const loadReport = useCallback(async () => {
    const request = ++reportRequest.current;
    try {
      const report = await api.report(sessionId);
      if (mounted.current && request === reportRequest.current) dispatch({ type: "report", report });
    } catch (error) {
      if (mounted.current && request === reportRequest.current) dispatch({ type: "report_error", message: errorMessage(error) });
    }
  }, [sessionId]);

  useEffect(() => {
    mounted.current = true;
    void refresh().catch(() => undefined);
    return () => { mounted.current = false; };
  }, [refresh]);

  const streamActive = Boolean(state.snapshot && !isTerminal(state.snapshot.state));

  const reconnect = useCallback(() => {
    dispatch({ type: "connection", connection: navigator.onLine ? "connecting" : "offline" });
    setConnectionEpoch((epoch) => epoch + 1);
    void refresh().catch(() => undefined);
  }, [refresh]);

  useEffect(() => {
    const offline = () => dispatch({ type: "connection", connection: "offline" });
    window.addEventListener("online", reconnect);
    window.addEventListener("offline", offline);
    return () => { window.removeEventListener("online", reconnect); window.removeEventListener("offline", offline); };
  }, [reconnect]);

  useEffect(() => {
    if (!streamActive) return;
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let reconciling = false;
    const source = new EventSource(api.eventsUrl(sessionId, lastSequence.current));
    const reconcile = () => {
      if (disposed || reconciling) return;
      reconciling = true;
      // EventSource retries its own subscription. While disconnected, a
      // bounded snapshot read also discovers a completed or restarted job.
      retryTimer = setTimeout(() => {
        if (disposed) return;
        if (!navigator.onLine) { reconciling = false; reconcile(); return; }
        void refresh().catch(() => undefined).finally(() => {
          reconciling = false;
          if (!disposed && source.readyState !== EventSource.OPEN) reconcile();
        });
      }, 3000);
    };
    const receive = (message: Event) => {
      if (!(message instanceof MessageEvent) || disposed) return;
      try {
        const value: unknown = JSON.parse(message.data);
        if (!envelope(value, sessionId)) throw new Error("Invalid event");
        lastSequence.current = Math.max(lastSequence.current, value.seq);
        dispatch({ type: "event", event: value });
        if (value.type === "report_ready") void loadReport();
        const snapshot = value.payload.snapshot;
        if (snapshot && isTerminal(snapshot.state)) {
          disposed = true;
          source.close();
          dispatch({ type: "connection", connection: "idle" });
          void refresh().catch(() => undefined);
        } else if (value.type === "error" || value.type === "status_changed") {
          void refresh().catch(() => undefined);
        }
      } catch {
        dispatch({ type: "connection", connection: "reconnecting" });
        void refresh().catch(() => undefined);
        reconcile();
      }
    };
    source.onopen = () => {
      if (disposed) return;
      dispatch({ type: "connection", connection: "connected" });
      if (retryTimer) clearTimeout(retryTimer);
      reconciling = false;
      void refresh().catch(() => undefined);
    };
    for (const type of ["status_changed", "utterance", "hint", "report_ready"]) source.addEventListener(type, receive);
    source.addEventListener("error", (event: Event) => {
      if (event instanceof MessageEvent) receive(event);
      else if (!disposed) {
        dispatch({ type: "connection", connection: navigator.onLine ? "reconnecting" : "offline" });
        reconcile();
      }
    });
    return () => { disposed = true; source.close(); if (retryTimer) clearTimeout(retryTimer); };
  }, [sessionId, streamActive, refresh, loadReport, connectionEpoch]);

  const reportState = state.snapshot?.report_state;
  useEffect(() => {
    if (reportState === "ready" || reportState === "failed") void loadReport();
  }, [reportState, loadReport]);

  return { ...state, refresh, reconnect, adoptSnapshot, loadReport };
}
