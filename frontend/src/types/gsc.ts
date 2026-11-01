/** Contracts of /api/v1/gsc (Google Search Console, read-only; Phase 6). */

export type ConnectionState =
  | "not_configured"
  | "disconnected"
  | "connected"
  | "reauthorization_required";

export interface GscStatus {
  state: ConnectionState;
  oauth_client_configured: boolean;
  missing_settings: string[];
  token_source: "stored" | "environment" | "none";
  can_store_token: boolean;
  token_storage: string;
  redirect_uri: string;
  scope: string;
  selected_property: string | null;
  connected_at: string | null;
  last_verified_at: string | null;
  last_error_category: string | null;
  scheduler_enabled: boolean;
  verified: boolean;
  position_note: string;
}

export interface Site {
  site_url: string;
  permission_level: string;
  verified: boolean;
}

export interface SyncRun {
  id: string;
  property: string;
  start_date: string;
  end_date: string;
  status: "planned" | "running" | "completed" | "partially_completed" | "failed";
  source: string;
  limits: { max_requests: number; rows_per_request: number };
  estimate: { requests_max: number; rows_max: number; days: number; notes: string[] };
  approval: { approved_at: string; max_requests: number } | null;
  usage: {
    requests?: number;
    rows_fetched?: number;
    rows_inserted?: number;
    rows_skipped?: number;
    daily_inserted?: number;
    stopped_reason?: string | null;
  };
  error_summary: string | null;
  created_at: string;
}

export interface DayPoint {
  date: string;
  clicks: number;
  impressions: number;
  ctr: number;
  position: number | null;
}

export interface Overview {
  property: string;
  has_data: boolean;
  first_date?: string;
  last_date?: string;
  start?: string;
  end?: string;
  series: DayPoint[];
  totals: { clicks: number; impressions: number; ctr: number; position: number | null } | null;
  days_with_data?: number;
  days_in_range?: number;
  note: string;
  position_note: string;
}

export interface TopRow {
  page: string | null;
  query: string | null;
  clicks: number;
  impressions: number;
  ctr: number;
  position: number | null;
}

export interface TopResponse {
  property: string;
  has_data: boolean;
  start?: string;
  end?: string;
  items: TopRow[];
}

export interface DataState {
  state: string;
  days_since_publish: number | null;
  note?: string;
}

export interface PageDetail {
  property: string;
  page: string;
  has_data: boolean;
  state: DataState;
  series: DayPoint[];
  queries: { query: string; clicks: number; impressions: number; ctr: number; position: number | null }[];
  position_note: string;
}

export interface Mapping {
  id: string;
  property: string;
  page_url: string;
  keyword: string | null;
  article_id: string | null;
  published_at: string | null;
  active: boolean;
}

export interface Analysis {
  id: string;
  property: string;
  status: string;
  window: { current_start?: string; current_end?: string; previous_start?: string; previous_end?: string };
  summary: { insights?: number; by_kind?: Record<string, number>; comparison_possible?: boolean };
  notes: string[];
  created_at: string;
}

export type Level = "high" | "medium" | "low";

export interface Insight {
  id: string;
  analysis_id: string;
  kind: string;
  subject_type: string;
  subject: string;
  evidence: Record<string, unknown>;
  impact: Level;
  confidence: Level;
  effort: Level;
  risk: Level;
  tier: string;
  rank: number;
  action: string;
  rationale: string;
  verification: string;
  basis: "measured" | "inferred";
  refresh_candidate: boolean;
  article_id: string | null;
  status: "open" | "accepted" | "dismissed";
}

export interface RefreshTask {
  id: string;
  insight_id: string | null;
  property: string;
  page_url: string;
  brief: { constraints?: string[]; proposals?: string[]; reason?: string };
  status: "proposed" | "accepted" | "draft_requested" | "draft_ready" | "dismissed";
  requires_new_approval: boolean;
  note: string | null;
}

export interface Schedule {
  id: string;
  name: string;
  property: string;
  kind: "sync" | "analyze";
  interval_hours: number;
  lookback_days: number;
  budget_requests: number;
  approved_by: string | null;
  approved_at: string | null;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
}
