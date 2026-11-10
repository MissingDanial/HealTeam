import type { CaseSummary, CommandReceipt, HealthStatus, School, SessionDetail, SessionHistory, SessionMode, SessionReport, SessionSnapshot } from "./types";
import type { CalibrationSummary, ReviewHistory, ReviewRecord, ReviewSubmission } from "./evaluation-types";

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly sessionId?: string;

  constructor(status: number, body: unknown) {
    super(`HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    const detail = record(body) && record(body.detail) ? body.detail : null;
    this.code = detail && typeof detail.code === "string" ? detail.code : undefined;
    this.sessionId = detail && typeof detail.session_id === "string" ? detail.session_id : undefined;
  }
}

export class RequestTimeoutError extends Error {
  constructor() {
    super("request_timeout");
    this.name = "RequestTimeoutError";
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof RequestTimeoutError) return "等待本地服务响应超时。已有内容仍保留，请重新连接或核对保存结果。";
  if (!(error instanceof ApiError)) return "暂时无法连接服务。你的内容仍保留着，请稍后重试。";
  if (error.code === "active_session_exists") return "已有一场尚未结束的会话，可以继续练习或先取消它。";
  if (error.code === "review_revision_conflict") return "这份复核已有新的修订。草稿仍保留，请核对最新版本后再保存。";
  if (error.code === "review_evidence_invalid" || error.code === "review_invalid_evidence") return "复核证据不符合要求，请从这次对话中选择对应的公开发言。";
  if (error.code === "review_binding_conflict") return "复核所依据的报告或量规与当前版本不同，请重新打开这份报告。草稿仍保留。";
  if (error.code === "review_unavailable") return "这份报告尚不支持人工复核。可以继续查看已保存的原始内容。";
  if (error.code?.includes("budget")) return "这次会话的模型调用预算已用完，已有对话保留供你回看。";
  if (error.code === "live_configuration_changed" || error.code === "model_configuration_changed") return "模型配置与这次会话创建时不同，请恢复原配置后再继续。已有记录仍可查看。";
  if (error.status === 404) return "找不到这次会话，它可能已被移除。";
  if (error.status === 409) return "会话状态已更新。已重新同步，你的草稿仍保留着。";
  if (error.status === 422) return "内容不符合要求，请检查填写内容后重试。";
  if (error.status === 503) return "服务暂时无法开始这项操作，请检查本地服务与模型配置后重试。";
  return "这次操作没有完成。已有内容不会被清空，请稍后重试。";
}

export function failureMessage(code?: string | null): string {
  if (code?.includes("budget")) return "这次会话的模型调用预算已用完，已有对话已保留。";
  if (code === "llm_http_401" || code === "llm_http_403" || code?.includes("authentication")) return "模型服务未通过身份验证，请检查本机服务配置。已有对话已保留。";
  if (code === "llm_http_429" || code?.includes("rate_limit")) return "模型服务暂时限制了请求，已有对话已保留。";
  if (code?.includes("truncated")) return "模型回应没有完整生成，已有对话已保留。";
  if (code?.includes("invalid_response") || code?.includes("validation") || code?.includes("invalid_output") || code?.includes("output_invalid") || code?.includes("evidence_invalid") || code === "llm_empty_response" || code === "llm_unsupported_response" || code === "llm_response_too_large") return "模型回应未通过格式或证据核对，已有对话已保留。";
  if (code === "model_context_limit") return "这次对话超出了配置的模型上下文范围，已有对话已保留。";
  if (code?.includes("refus")) return "模型服务没有完成这次回应，已有对话已保留。";
  if (code === "report_timeout") return "复盘生成超时，已有对话已保留。";
  if (code?.includes("timeout")) return "生成回应超时，已有对话已保留。";
  if (code?.includes("connection") || code?.includes("http_5")) return "模型服务暂时无法完成回应，已有对话已保留。";
  if (code?.includes("requires") || code?.includes("configuration")) return "模型配置尚不完整，已有对话已保留。";
  return "这次会话未能完成，已有对话已保留。";
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      cache: "no-store",
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    });
    const text = await response.text();
    let data: unknown = null;
    if (text) {
      try { data = JSON.parse(text); } catch { throw new ApiError(response.status || 502, null); }
    }
    if (!response.ok) throw new ApiError(response.status, data);
    return data as T;
  } catch (error) {
    if (controller.signal.aborted) throw new RequestTimeoutError();
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export function receiptSnapshot(receipt: CommandReceipt): SessionSnapshot | undefined {
  if ("state" in receipt && "last_event_seq" in receipt) return receipt as SessionSnapshot;
  if ("snapshot" in receipt && record(receipt.snapshot)) return receipt.snapshot as unknown as SessionSnapshot;
  return undefined;
}

const sessionPath = (id: string) => `/api/v1/sessions/${encodeURIComponent(id)}`;
const post = <T>(path: string, body: object = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => request<HealthStatus>("/healthz"),
  readiness: () => request<HealthStatus>("/readyz"),
  cases: () => request<CaseSummary[]>("/api/v1/cases"),
  history: (limit = 8, offset = 0) => request<SessionHistory>(`/api/v1/sessions?limit=${limit}&offset=${offset}`),
  create: (body: { client_request_id: string; case_id: string; school: School; mode: SessionMode; max_rounds: number }) => post<SessionSnapshot>("/api/v1/sessions", body),
  session: (id: string) => request<SessionDetail>(sessionPath(id)),
  message: (id: string, body: { client_message_id: string; expected_turn: number; content: string }) => post<CommandReceipt>(`${sessionPath(id)}/messages`, body),
  finish: (id: string) => post<CommandReceipt>(`${sessionPath(id)}/finish`),
  cancel: (id: string) => post<CommandReceipt>(`${sessionPath(id)}/cancel`),
  report: (id: string) => request<SessionReport>(`/api/v1/reports/${encodeURIComponent(id)}`),
  retryReport: (id: string) => post<CommandReceipt>(`/api/v1/reports/${encodeURIComponent(id)}/retry`),
  reviews: (id: string) => request<ReviewHistory>(`/api/v1/reports/${encodeURIComponent(id)}/reviews`),
  saveReview: (id: string, body: ReviewSubmission) => post<ReviewRecord>(`/api/v1/reports/${encodeURIComponent(id)}/reviews`, body),
  calibration: () => request<CalibrationSummary>("/api/v1/calibration"),
  calibrationExport: () => request<unknown>("/api/v1/calibration/export"),
  eventsUrl: (id: string, after: number) => `${sessionPath(id)}/events?after=${after}`,
};
