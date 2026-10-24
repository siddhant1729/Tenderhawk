// Mirrors api/schemas.py and PLAN.md §3.3–§3.5. Keep in sync with the backend.

export type Verdict = "ELIGIBLE" | "CHECK_DOCS" | "NOT_ELIGIBLE";
export type AgentName = "gem" | "cppp" | "haryana" | "corrigenda" | "all";
export type EventType = "started" | "step" | "result" | "error" | "done";

export interface Profile {
  id: number;
  name: string;
  phone: string | null;
  email: string | null;
  products: string[];
  keywords: string[];
  hsn_codes: string[];
  annual_turnover: number | null;
  years_in_business: number | null;
  certifications: string[];
  states_served: string[];
}

export type ProfileInput = Omit<Profile, "id">;

export interface StepEvent {
  sweep_id: string;
  agent: AgentName;
  type: EventType;
  step: number;
  message: string;
  count: number | null;
  direct_url: string | null;
  ts: string;
}

export interface Tender {
  id: number;
  portal: string;
  bid_no: string;
  title: string;
  item: string | null;
  buyer: string | null;
  quantity: string | null;
  est_value: number | null;
  end_date: string;
  source_url: string;
  doc_url: string | null;
}

export interface Excerpt {
  text: string;
  highlight_start: number;
  highlight_end: number;
  prefix_truncated: boolean;
  suffix_truncated: boolean;
}

export interface Reason {
  kind: "ok" | "info" | "warn" | "fail";
  criterion: string;
  text: string;
  source_clause: string | null;
  source_url?: string | null;
  excerpt?: Excerpt | null;
}

export interface MatchSummary {
  id: number;
  profile_id: number;
  verdict: Verdict;
  fit_score: number | null;
  relevant: boolean;
  reasons: Reason[];
  missing_docs: string[];
  alerted_at: string | null;
  tender: Tender;
}

export interface Corrigendum {
  bid_no: string;
  summary: string;
  source_url: string;
  new_end_date?: string | null;
  extends_to?: string | null;
  found_at?: string;
}

export interface MatchDetail extends MatchSummary {
  eligibility: Record<string, { value: unknown; source_clause: string | null }> | null;
  corrigenda: Corrigendum[];
}

export interface Health {
  db: boolean;
  redis: boolean;
  fallback_mode: boolean;
  next_corrigenda_check: string | null;
  now: string;                                   // the API's clock (frozen demo clock in fallback mode)
  clock: "real" | "DEMO_NOW" | "fixture recorded_at";
}

export interface AlertResult {
  match_id: number;
  sent: boolean;
  channels: { whatsapp: string; email: string };
  preview: string;
}
