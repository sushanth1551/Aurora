/** Contracts of /api/v1/seo/keywords (keyword research, Phase 3). */

export type RunStatus = "planned" | "running" | "completed" | "partially_completed" | "failed";
export type Level = "high" | "medium" | "low";
export type Action =
  | "write_new"
  | "improve_existing"
  | "monitor"
  | "low_evidence"
  | "deprioritized";

export interface RunEstimate {
  serpapi_searches_max: number;
  cached_snapshots_possible: number;
  estimated_cost: number | null;
  cost_note: string;
  notes: string[];
}

export interface RunUsage {
  serpapi_searches?: number;
  cache_hits?: number;
  failures?: number;
  estimated_cost?: number | null;
  stopped_reason?: string | null;
}

export interface Run {
  id: string;
  site_url: string;
  host: string;
  status: RunStatus;
  params: {
    seeds: string[];
    site_topics: string[];
    audit_id: string | null;
    country: string | null;
    language: string | null;
    device: string | null;
    planned_queries: string[];
    include_expansions: boolean;
  };
  limits: { max_queries: number; cache_ttl_hours: number; min_interval_seconds: number };
  estimate: RunEstimate;
  approval: { approved_at: string; max_searches: number } | null;
  usage: RunUsage;
  summary: {
    candidates?: number;
    searched?: number;
    unsearched_candidates?: number;
    clusters?: number;
    opportunities?: number;
    opportunities_by_action?: Record<string, number>;
    competitors?: number;
    gaps?: number;
    ai_overview_present?: number;
    metrics_note?: string;
  };
  gap_status: string | null;
  error_summary: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface MetricValue {
  value: number | null;
  status: "measured" | "not_available";
  source: string | null;
}

export interface SerpEvidence {
  snapshot_id: string | null;
  query: string;
  organic_count: number;
  top_domains: string[];
  site_position: number | null;
  paa_count: number;
  related_count: number;
  featured_snippet_domain: string | null;
  ai_overview: string;
  ai_overview_cites_site: boolean | null;
  shopping: boolean;
  ads: number;
  cache_hit: boolean;
}

export interface Opportunity {
  id: string;
  run_id: string;
  keyword: string;
  rank: number;
  action: Action;
  intent: string | null;
  intent_basis: string | null;
  cluster: string | null;
  impact: Level;
  confidence: Level;
  effort: Level;
  risk: Level;
  rationale: string[];
  serp: SerpEvidence;
  gap_terms: string[];
  questions: string[];
  metrics: Record<string, MetricValue>;
  basis: string;
  selected: boolean;
  selected_at: string | null;
}

export interface Competitor {
  domain: string;
  appearances: number;
  keywords: string[];
  best_position: number;
  positions: Record<string, number>;
  classification: string;
}

export interface GapEvidence {
  domain: string;
  url: string;
  query: string;
  position: number;
  excerpt: string;
}

export interface Gap {
  term: string;
  kind: "phrase" | "question";
  competitor_domains: string[];
  queries: string[];
  evidence: GapEvidence[];
  basis: string;
}

export interface GapResponse {
  status: string;
  note: string;
  items: Gap[];
}

export interface Comparison {
  domains: string[];
  unknown_domains: string[];
  keywords: string[];
  shared_keywords: string[];
  matrix: Record<string, Record<string, number>>;
  only_for: Record<string, string[]>;
  basis: string;
}

export interface SerpOrganic {
  position: number;
  title: string;
  url: string;
  snippet: string | null;
  domain: string | null;
}

export interface SerpSnapshot {
  id: string;
  query: string;
  country: string | null;
  language: string | null;
  device: string | null;
  observed_at: string;
  organic: SerpOrganic[];
  features: {
    people_also_ask: { question: string; snippet: string | null; domain: string | null }[];
    related_searches: string[];
    featured_snippet: { kind: string; text: string | null; domain: string | null } | null;
    ai_overview: { status: string; cited_domains: string[] };
    shopping_results: boolean;
    ads_top: number;
    ads_bottom: number;
  };
}

export interface SelectedOpportunity {
  opportunity_id: string;
  run_id: string;
  host: string;
  keyword: string;
  intent: string | null;
  action: string;
  cluster: string | null;
  gap_terms: string[];
  questions: string[];
  serp_snapshot_id: string | null;
  selected_at: string | null;
}

export interface PlanInput {
  site_url: string;
  seeds: string[];
  country?: string;
  language?: string;
  device?: "desktop" | "mobile" | "tablet";
  max_queries?: number;
  include_expansions: boolean;
}
