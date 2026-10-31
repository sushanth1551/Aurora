import type { ResearchRequest, ResearchResponse } from "../types/research";
import { apiFetch } from "./api";

export async function submitResearch(
  request: ResearchRequest,
): Promise<ResearchResponse> {
  return apiFetch<ResearchResponse>("/api/v1/research", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
