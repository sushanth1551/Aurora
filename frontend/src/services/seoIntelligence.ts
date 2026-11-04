import { apiFetch } from "./api";

const base = "/api/v1/seo/intelligence";

export interface SiteSummary {
  id: string;
  name: string;
  domain: string;
}

export interface FindingRow {
  priority: string;
  category: string;
  issue: string;
  status: string;
  recommended_action: string;
  key: string;
}

/** The dashboard document served by the API; every list is copied from stored runs. */
export interface InsightDashboard {
  site_id: string;
  open_findings: {
    total: number;
    by_priority: Record<string, number>;
    by_category: Record<string, number>;
  };
  top_findings: FindingRow[];
  updated: Record<string, string | null>;
  opportunities: Array<{
    kind: string;
    query: string;
    page: string;
    impressions: number;
    position: number;
    ctr: number;
    extra_clicks: number | null;
    extra_leads: number | null;
  }>;
  content_trends: Array<{ page: string; status: string; decision: string; merge_with: string | null }>;
  alerts: Array<{ kind: string; subject: string; detail: string; priority: string }>;
  market_ranks: Array<{
    keyword: string;
    market: string;
    position: number | null;
    previous_position: number | null;
    movement: number | null;
    impressions: number;
  }>;
  serp_checks: Array<{
    keyword: string;
    market: string;
    device: string;
    our_position: number | null;
    featured_snippet_domain: string | null;
    ai_overview: string;
    ai_overview_cites_site: boolean | null;
  }>;
  ai_crawlers: Array<{ agent: string; operator: string; purpose: string; blocked: boolean }>;
  ai_share_of_voice: {
    engines: Record<string, { answers: number; cited: number; mentioned: number; share_cited_pct: number }>;
  } | null;
  competitor_changes: Array<{
    domain: string;
    new_count: number;
    updated_count: number;
    removed_count: number;
    uncovered: string[];
  }>;
  crawl: { coverage?: { pages_audited: number }; soft_404_status?: number | null };
}

export async function listSites(): Promise<SiteSummary[]> {
  const data = await apiFetch<{ items: SiteSummary[] }>(`${base}/websites`);
  return data.items;
}

export function getDashboard(siteId: string): Promise<InsightDashboard> {
  return apiFetch<InsightDashboard>(`${base}/websites/${encodeURIComponent(siteId)}/insights`);
}
