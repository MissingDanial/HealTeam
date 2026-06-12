/// HealTeam web types — mirror packages/memory/schema.py Pydantic models.
///
/// W1: minimal slice needed by the landing page.
/// W2: add the full set (Session, Event, Hint, etc.) for live therapy UI.
export type Actor =
  | "doctor"
  | "psychiatrist"
  | "observer"
  | "father"
  | "mother"
  | "patient"
  | "trainee"
  | "system";

export type School = "structural" | "bowen" | "satir";
export type SessionStatus = "pending" | "running" | "ended" | "reported";

export interface AgentInfo {
  name: string;
  role: string;
  speaks_in_session: boolean;
}
