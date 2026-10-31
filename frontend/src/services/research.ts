import type { ResearchRequest, ResearchResponse } from "../types/research";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

interface ErrorBody {
  error?: { message?: string };
  failure?: { message?: string } | null;
}

async function describeFailure(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ErrorBody;
    const message = body.error?.message ?? body.failure?.message;
    if (message) return `${message} (HTTP ${response.status})`;
  } catch {
    // Non-JSON error body; fall through to the generic message.
  }
  return `Research request failed with status ${response.status}`;
}

export async function submitResearch(
  request: ResearchRequest,
): Promise<ResearchResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(await describeFailure(response));
  }

  return (await response.json()) as ResearchResponse;
}
