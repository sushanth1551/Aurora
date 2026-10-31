import type { ResearchAcknowledgement, ResearchRequest } from "../types/research";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export async function submitResearch(
  request: ResearchRequest,
): Promise<ResearchAcknowledgement> {
  const response = await fetch(`${apiBaseUrl}/api/v1/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`Research request failed with status ${response.status}`);
  }

  return (await response.json()) as ResearchAcknowledgement;
}
