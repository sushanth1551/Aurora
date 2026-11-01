import type {
  Analysis,
  GscStatus,
  Insight,
  Mapping,
  Overview,
  PageDetail,
  RefreshTask,
  Schedule,
  Site,
  SyncRun,
  TopResponse,
} from "../types/gsc";
import { apiFetch } from "./api";

const base = "/api/v1/gsc";

function post<T>(path: string, body: unknown = {}): Promise<T> {
  return apiFetch<T>(`${base}${path}`, { method: "POST", body: JSON.stringify(body) });
}

function patch<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(`${base}${path}`, { method: "PATCH", body: JSON.stringify(body) });
}

function query(params: Record<string, string | number | undefined>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  return pairs.length
    ? "?" + pairs.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&")
    : "";
}

// Connection ---------------------------------------------------------------------------

export const getStatus = () => apiFetch<GscStatus>(`${base}/status`);
export const startAuthorization = () =>
  post<{ authorization_url: string; expires_in_seconds: number }>("/oauth/start");
export const verifyConnection = () => post<GscStatus>("/connection/verify");
export const disconnect = (revoke: boolean) => post<GscStatus>("/disconnect", { revoke });
export const listProperties = () => apiFetch<Site[]>(`${base}/properties`);
export const selectProperty = (siteUrl: string) =>
  apiFetch<GscStatus>(`${base}/property`, {
    method: "PUT",
    body: JSON.stringify({ site_url: siteUrl }),
  });

// Sync ---------------------------------------------------------------------------------

export const planSync = (maxRequests?: number) =>
  post<SyncRun>("/syncs", maxRequests ? { max_requests: maxRequests } : {});
export const runSync = (id: string, maxRequests: number) =>
  post<SyncRun>(`/syncs/${id}/run`, { approve: true, max_requests: maxRequests });
export const listSyncs = () => apiFetch<SyncRun[]>(`${base}/syncs?limit=10`);

// Stored performance (no Google call) ----------------------------------------------------

export const getOverview = (start?: string, end?: string) =>
  apiFetch<Overview>(`${base}/performance/overview${query({ start, end })}`);
export const getTop = (by: "pages" | "queries", limit = 25) =>
  apiFetch<TopResponse>(`${base}/performance/${by}${query({ limit })}`);
export const getPage = (url: string) =>
  apiFetch<PageDetail>(`${base}/page${query({ url })}`);

// Mappings -------------------------------------------------------------------------------

export const listMappings = () => apiFetch<Mapping[]>(`${base}/mappings`);
export const addMapping = (input: {
  page_url: string;
  keyword?: string;
  article_id?: string;
  published_at?: string;
}) => post<Mapping>("/mappings", input);
export const setMappingActive = (id: string, active: boolean) =>
  patch<Mapping>(`/mappings/${id}`, { active });

// Analysis ---------------------------------------------------------------------------------

export const analyze = () => post<Analysis>("/analysis", {});
export const listAnalyses = () => apiFetch<Analysis[]>(`${base}/analysis?limit=5`);
export const getInsights = (analysisId: string) =>
  apiFetch<Insight[]>(`${base}/analysis/${analysisId}/insights`);
export const setInsightStatus = (id: string, status: "open" | "accepted" | "dismissed") =>
  patch<Insight>(`/insights/${id}`, { status });
export const createRefreshTask = (insightId: string) =>
  post<RefreshTask>(`/insights/${insightId}/refresh-task`);
export const listTasks = () => apiFetch<RefreshTask[]>(`${base}/refresh-tasks`);
export const updateTask = (id: string, status: string, note?: string) =>
  patch<RefreshTask>(`/refresh-tasks/${id}`, { status, ...(note ? { note } : {}) });

// Schedules -----------------------------------------------------------------------------------

export const listSchedules = () => apiFetch<Schedule[]>(`${base}/schedules`);
export const createSchedule = (input: {
  name: string;
  kind: "sync" | "analyze";
  interval_hours: number;
  lookback_days: number;
  budget_requests: number;
}) => post<Schedule>("/schedules", input);
export const approveSchedule = (id: string, budgetRequests: number) =>
  post<Schedule>(`/schedules/${id}/approve`, { approve: true, budget_requests: budgetRequests });
export const setScheduleEnabled = (id: string, enabled: boolean) =>
  post<Schedule>(`/schedules/${id}/enabled`, { enabled });
