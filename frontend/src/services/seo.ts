import type {
  Audit,
  Finding,
  PageSummary,
  Paged,
  Recommendation,
} from "../types/seo";
import { apiFetch } from "./api";

const base = "/api/v1/seo/audits";

export function planAudit(url: string, maxPages?: number): Promise<Audit> {
  return apiFetch<Audit>(base, {
    method: "POST",
    body: JSON.stringify(maxPages ? { url, max_pages: maxPages } : { url }),
  });
}

/** Approves the plan's ceiling and runs the audit; the request waits until it ends. */
export function runAudit(id: string, maxFirecrawlScrapes: number): Promise<Audit> {
  return apiFetch<Audit>(`${base}/${id}/run`, {
    method: "POST",
    body: JSON.stringify({ approve: true, max_firecrawl_scrapes: maxFirecrawlScrapes }),
  });
}

export function listAudits(): Promise<Paged<Audit>> {
  return apiFetch<Paged<Audit>>(`${base}?limit=20`);
}

export function getAudit(id: string): Promise<Audit> {
  return apiFetch<Audit>(`${base}/${id}`);
}

export interface AuditResults {
  findings: Finding[];
  recommendations: Recommendation[];
  pages: PageSummary[];
  pagesTotal: number;
}

export async function getAuditResults(id: string): Promise<AuditResults> {
  const [findings, recommendations, pages] = await Promise.all([
    apiFetch<Paged<Finding>>(`${base}/${id}/findings`),
    apiFetch<Paged<Recommendation>>(`${base}/${id}/recommendations`),
    apiFetch<Paged<PageSummary>>(`${base}/${id}/pages?limit=200`),
  ]);
  return {
    findings: findings.items,
    recommendations: recommendations.items,
    pages: pages.items,
    pagesTotal: pages.total,
  };
}
