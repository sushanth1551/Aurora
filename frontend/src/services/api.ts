/**
 * Shared API client. Every protected route needs the local API token
 * (AURORA_API_TOKEN). The token is entered by the user and kept in
 * sessionStorage for this browser tab only; it is never part of the bundle.
 */

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const TOKEN_KEY = "aurora.apiToken";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

interface ErrorBody {
  error?: { code?: string; message?: string };
  failure?: { message?: string } | null;
}

export function getApiToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // storage blocked: the user re-enters the token
  }
}

export function setApiToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Storage unavailable; the token lives only until the page reloads.
  }
}

export function clearApiToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing stored.
  }
}

async function failureFrom(response: Response): Promise<ApiError> {
  let message = `Request failed with status ${response.status}`;
  let code: string | undefined;
  try {
    const body = (await response.json()) as ErrorBody;
    code = body.error?.code;
    const detail = body.error?.message ?? body.failure?.message;
    if (detail) message = `${detail} (HTTP ${response.status})`;
  } catch {
    // Non-JSON error body; keep the generic message.
  }
  return new ApiError(message, response.status, code);
}

/** fetch() against the API with the stored token; non-2xx responses throw ApiError. */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  token: string | null = getApiToken(),
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(
      "The AURORA API is not reachable. Is the backend running?",
      0,
    );
  }
  if (!response.ok) throw await failureFrom(response);
  return (await response.json()) as T;
}

/** Confirms a token with the API before it is stored. */
export async function verifyApiToken(token: string): Promise<void> {
  await apiFetch<{ authenticated: boolean }>("/api/v1/auth/session", {}, token);
}
