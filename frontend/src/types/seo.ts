/** Contracts of /api/v1/seo/audits (technical SEO, Phase 2). */

export type AuditStatus =
  | "planned"
  | "running"
  | "completed"
  | "partially_completed"
  | "failed";

export interface AuditEstimate {
  firecrawl_scrapes_max: number;
  direct_requests_max: number;
  notes: string[];
}

export interface AuditCoverage {
  pages_audited?: number;
  pages_discovered?: number;
  pages_not_fetched?: number;
  sitemap_urls_found?: number;
  link_targets_found?: number;
  link_targets_checked?: number;
  stopped_reason?: string | null;
  not_measured?: string[];
}

export interface AuditUsage {
  firecrawl_scrapes?: number;
  firecrawl_failures?: number;
  direct_requests?: number;
}

export interface Audit {
  id: string;
  site_url: string;
  host: string;
  status: AuditStatus;
  limits: {
    max_pages: number;
    max_link_checks: number;
    max_sitemaps: number;
    max_duration_seconds: number;
  };
  estimate: AuditEstimate;
  approval: { approved_at: string; max_firecrawl_scrapes: number } | null;
  usage: AuditUsage;
  coverage: AuditCoverage;
  site_files: {
    robots?: { url: string; outcome: string; status: number | null; applies: string } | null;
    sitemaps?: { url: string; kind: string; url_count: number; error: string | null }[];
  };
  summary: {
    findings?: number;
    recommendations?: number;
    findings_by_severity?: Record<string, number>;
    pages_by_indexability?: Record<string, number>;
  };
  error_summary: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export type Level = "high" | "medium" | "low";
export type Severity = Level | "info";

export interface Evidence {
  url: string;
  source: string;
  observed: Record<string, unknown>;
  observed_at: string | null;
  html_sha256: string | null;
}

export interface Finding {
  id: string;
  check_id: string;
  category: string;
  severity: Severity;
  title: string;
  detail: string;
  affected_urls: string[];
  affected_count: number;
  evidence: Evidence[];
  evidence_state: string;
  skill_refs: string[];
}

export interface Recommendation {
  id: string;
  finding_id: string;
  check_id: string;
  action: string;
  rationale: string;
  impact: Level;
  confidence: Level;
  effort: Level;
  risk: Level;
  tier: string;
  rank: number;
  affected_count: number;
  verification: string;
  basis: string;
}

export interface PageSummary {
  id: string;
  url: string;
  final_url: string | null;
  status_code: number | null;
  status_source: string;
  fetch_status: string;
  fetch_source: string;
  depth: number;
  title: string | null;
  indexability: string;
  indexability_reasons: string[];
  googlebot_allowed: boolean | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
}
