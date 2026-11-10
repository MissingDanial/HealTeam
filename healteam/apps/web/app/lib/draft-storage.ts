export type MessageAttempt = { client_message_id: string; expected_turn: number; content: string };
type SessionDraft = { content: string; attempt: MessageAttempt | null };
type CreateDraft = { case_id: string; school: "structural" | "bowen" | "satir"; mode: "training" | "demo"; max_rounds: number; client_request_id: string };

const sessionKey = (sessionId: string) => `healteam:session-draft:v1:${sessionId}`;
const createKey = "healteam:create-draft:v1";
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const memory = new Map<string, string | null>();
const changeEvent = "healteam:tab-draft-change";

function raw(key: string): string | null {
  if (memory.has(key)) return memory.get(key) ?? null;
  try { return window.sessionStorage.getItem(key); } catch { return null; }
}

function read(key: string): unknown {
  try { return JSON.parse(raw(key) ?? "null"); } catch { return null; }
}

function write(key: string, value: object | null) {
  const next = value ? JSON.stringify(value) : null;
  if (raw(key) === next) return;
  memory.set(key, next);
  try {
    if (next) window.sessionStorage.setItem(key, next);
    else window.sessionStorage.removeItem(key);
  } catch { /* Memory state still works when browser storage is unavailable. */ }
  window.dispatchEvent(new Event(changeEvent));
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function readSessionDraft(sessionId: string): SessionDraft | null {
  return parseSessionDraft(read(sessionKey(sessionId)));
}

function parseSessionDraft(value: unknown): SessionDraft | null {
  if (!record(value) || typeof value.content !== "string" || value.content.length > 2000) return null;
  const pending = value.attempt;
  const attempt: MessageAttempt | null = record(pending) && typeof pending.client_message_id === "string" && uuid.test(pending.client_message_id) && Number.isInteger(pending.expected_turn) && Number(pending.expected_turn) >= 1 && Number(pending.expected_turn) <= 30 && typeof pending.content === "string" && pending.content.length > 0 && pending.content.length <= 2000
    ? { client_message_id: pending.client_message_id, expected_turn: Number(pending.expected_turn), content: pending.content }
    : null;
  return { content: attempt?.content ?? value.content, attempt };
}

function subscribe(listener: () => void) {
  window.addEventListener(changeEvent, listener);
  window.addEventListener("storage", listener);
  return () => { window.removeEventListener(changeEvent, listener); window.removeEventListener("storage", listener); };
}

const serverSnapshot = () => null;
const hydrated = () => true;
const serverHydrated = () => false;

export function useSessionDraft(sessionId: string) {
  const snapshot = useCallback(() => raw(sessionKey(sessionId)), [sessionId]);
  const text = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  const ready = useSyncExternalStore(subscribe, hydrated, serverHydrated);
  const draft = useMemo(() => {
    try { return parseSessionDraft(JSON.parse(text ?? "null")); } catch { return null; }
  }, [text]);
  return { ready, content: draft?.content ?? "", attempt: draft?.attempt ?? null };
}

export function saveSessionDraft(sessionId: string, content: string, attempt: MessageAttempt | null) {
  write(sessionKey(sessionId), content || attempt ? { content, attempt } : null);
}

export function resolveSessionAttempt(sessionId: string, commandId: string, keepContent = false) {
  const current = readSessionDraft(sessionId);
  if (current?.attempt?.client_message_id === commandId) saveSessionDraft(sessionId, keepContent ? current.content : "", null);
}

export function readCreateDraft(): CreateDraft | null {
  return parseCreateDraft(read(createKey));
}

function parseCreateDraft(value: unknown): CreateDraft | null {
  if (!record(value) || typeof value.case_id !== "string" || typeof value.client_request_id !== "string" || !uuid.test(value.client_request_id) || !["structural", "bowen", "satir"].includes(String(value.school)) || !["training", "demo"].includes(String(value.mode)) || !Number.isInteger(value.max_rounds) || Number(value.max_rounds) < 1 || Number(value.max_rounds) > 30) return null;
  return value as CreateDraft;
}

const createSnapshot = () => raw(createKey);

export function useCreateDraft() {
  const text = useSyncExternalStore(subscribe, createSnapshot, serverSnapshot);
  return useMemo(() => {
    try { return parseCreateDraft(JSON.parse(text ?? "null")); } catch { return null; }
  }, [text]);
}

export function saveCreateDraft(value: CreateDraft | null) { write(createKey, value); }

export function resolveCreateAttempt(commandId: string) {
  if (readCreateDraft()?.client_request_id === commandId) saveCreateDraft(null);
}
import { useCallback, useMemo, useSyncExternalStore } from "react";

