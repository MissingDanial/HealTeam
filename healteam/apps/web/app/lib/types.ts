import type { Assessment, TrialRubric } from "./evaluation-types";

export type Actor = "doctor" | "psychiatrist" | "observer" | "father" | "mother" | "patient" | "trainee" | "system";
export type School = "structural" | "bowen" | "satir";
export type SessionMode = "training" | "demo";
export type ModelMode = "mock" | "live";
export type SessionState = "created" | "awaiting_input" | "running" | "cancelling" | "completed" | "cancelled" | "failed" | "interrupted";
export type SessionPhase = "simulation" | "supervision" | "report";
export type ReportState = "not_started" | "generating" | "ready" | "failed" | "cancelled";
export type StreamEventType = "status_changed" | "utterance" | "hint" | "report_ready" | "error";

export interface CaseSummary {
  case_id: string;
  title: string;
  presenting_problem: string;
}

export interface HealthStatus {
  status: string;
  model_mode?: ModelMode;
  provider?: string;
  model?: string | null;
}

export interface ModelUsage {
  requests: number;
  input_tokens: number;
  output_tokens: number;
  unknown_requests: number;
  reserved_tokens: number;
  max_calls: number;
  token_budget: number;
}

export interface SessionSnapshot {
  session_id: string;
  case_id: string;
  case_version: string;
  school: School;
  mode: SessionMode;
  model_mode: ModelMode;
  state: SessionState;
  phase: SessionPhase;
  max_rounds: number;
  current_round: number;
  last_event_seq: number;
  report_state: ReportState;
  completed_rounds?: number;
  created_at?: string;
  updated_at?: string;
  error_code?: string | null;
  provider?: string | null;
  model?: string | null;
  usage?: ModelUsage;
}

export interface EventEnvelope {
  event_id: string;
  session_id: string;
  seq: number;
  round: number;
  type: StreamEventType;
  actor: Actor;
  source: "human" | "ai" | "system";
  visibility: "public" | "learner" | "supervisor" | "internal";
  timestamp: string;
  payload: {
    content?: string;
    snapshot?: SessionSnapshot;
    code?: string;
    client_message_id?: string;
    [key: string]: unknown;
  };
}

export interface SessionDetail extends SessionSnapshot {
  events: EventEnvelope[];
}

export interface SessionHistory {
  items: SessionSnapshot[];
  total: number;
}

export interface SessionReport {
  session_id: string;
  status: ReportState;
  content?: string | null;
  kind: string;
  rubric?: TrialRubric | null;
  assessment?: Assessment | null;
  generated_at?: string | null;
  structured?: {
    summary: string;
    observations: { text: string; evidence_seq: number[] }[];
    next_steps: { text: string; evidence_seq: number[] }[];
  } | null;
}

export type CommandReceipt = SessionSnapshot | {
  snapshot?: SessionSnapshot;
  session_id?: string;
  client_message_id?: string;
  [key: string]: unknown;
};

export const SCHOOLS: { value: School; label: string; subtitle: string; description: string }[] = [
  { value: "structural", label: "结构式", subtitle: "Structural", description: "关注家庭边界与互动" },
  { value: "bowen", label: "Bowen", subtitle: "Family systems", description: "观察情绪与三角关系" },
  { value: "satir", label: "Satir", subtitle: "Experiential", description: "练习一致性的沟通" },
];

export const SCHOOL_LABELS: Record<School, string> = {
  structural: "结构式家庭治疗", bowen: "Bowen 家庭系统", satir: "Satir 家庭治疗",
};

export const ACTOR_LABELS: Record<Actor, string> = {
  trainee: "你 · 治疗师", doctor: "AI 治疗师", father: "父亲", mother: "母亲",
  patient: "来访者", observer: "观察者", psychiatrist: "督导", system: "系统",
};

export function providerLabel(provider?: string | null): string {
  if (!provider) return "";
  return ({ openai: "OpenAI", anthropic: "Anthropic", deepseek: "DeepSeek" } as Record<string, string>)[provider] ?? provider;
}

export function isTerminal(state: SessionState): boolean {
  return ["completed", "cancelled", "failed", "interrupted"].includes(state);
}

export function completedRounds(snapshot: SessionSnapshot): number {
  if (typeof snapshot.completed_rounds === "number") return snapshot.completed_rounds;
  if (snapshot.state === "awaiting_input" || snapshot.state === "completed" || snapshot.phase !== "simulation") return snapshot.current_round;
  return Math.max(0, snapshot.current_round - 1);
}

export function sessionLabel(snapshot: SessionSnapshot): string {
  if (snapshot.state === "running") {
    if (snapshot.phase === "supervision") return "正在复盘";
    if (snapshot.phase === "report") return "正在保存报告";
    return snapshot.mode === "demo" ? "正在演示" : "家庭正在回应";
  }
  return ({ created: "正在准备", awaiting_input: "轮到你发言", cancelling: "正在停止", completed: "已完成", cancelled: "已取消", failed: "未完成", interrupted: "运行已中断" } as const)[snapshot.state];
}

export function formatTime(value?: string | null): string {
  if (!value || Number.isNaN(new Date(value).getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
