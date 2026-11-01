import type {
  Comparison,
  Competitor,
  GapResponse,
  Opportunity,
  PlanInput,
  Run,
  SelectedOpportunity,
  SerpSnapshot,
} from "../types/keywords";
import { apiFetch } from "./api";

const base = "/api/v1/seo/keywords";

interface Items<T> {
  items: T[];
  total?: number;
}

export function planRun(input: PlanInput): Promise<Run> {
  return apiFetch<Run>(`${base}/runs`, { method: "POST", body: JSON.stringify(input) });
}

/** Approves the plan's search ceiling and runs; the request waits until the run ends. */
export function startRun(id: string, maxSearches: number): Promise<Run> {
  return apiFetch<Run>(`${base}/runs/${id}/run`, {
    method: "POST",
    body: JSON.stringify({ approve: true, max_searches: maxSearches }),
  });
}

export async function listRuns(): Promise<Run[]> {
  return (await apiFetch<Items<Run>>(`${base}/runs?limit=20`)).items;
}

export function getRun(id: string): Promise<Run> {
  return apiFetch<Run>(`${base}/runs/${id}`);
}

export interface RunResults {
  opportunities: Opportunity[];
  competitors: Competitor[];
  gaps: GapResponse;
}

export async function getRunResults(id: string): Promise<RunResults> {
  const [opportunities, competitors, gaps] = await Promise.all([
    apiFetch<Items<Opportunity>>(`${base}/runs/${id}/opportunities`),
    apiFetch<Items<Competitor>>(`${base}/runs/${id}/competitors`),
    apiFetch<GapResponse>(`${base}/runs/${id}/gaps`),
  ]);
  return { opportunities: opportunities.items, competitors: competitors.items, gaps };
}

export function compareCompetitors(id: string, domains: string[]): Promise<Comparison> {
  const query = domains.map((d) => `domains=${encodeURIComponent(d)}`).join("&");
  return apiFetch<Comparison>(`${base}/runs/${id}/competitors/compare?${query}`);
}

export function getSerp(snapshotId: string): Promise<SerpSnapshot> {
  return apiFetch<SerpSnapshot>(`${base}/serp/${snapshotId}`);
}

export function setSelection(opportunityId: string, selected: boolean): Promise<Opportunity> {
  return apiFetch<Opportunity>(`${base}/opportunities/${opportunityId}/selection`, {
    method: "POST",
    body: JSON.stringify({ selected }),
  });
}

export async function listSelected(host?: string): Promise<SelectedOpportunity[]> {
  const query = host ? `?host=${encodeURIComponent(host)}` : "";
  return (await apiFetch<Items<SelectedOpportunity>>(`${base}/selected${query}`)).items;
}
