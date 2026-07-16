/**
 * Shared HTTP helpers for authenticated API calls.
 *
 * Responsibilities:
 * - attach Authorization Bearer from localStorage
 * - map 401 responses to AuthError and force a session expiry event
 * - keep SSE callers working (returns raw Response)
 */

import { AuthError, clearAuth } from "./authApi";

export const AUTH_EXPIRED_EVENT = "auth:expired";

type AuthHeadersOptions = {
  /** When false, omit Content-Type (FormData uploads). Default true. */
  json?: boolean;
  /** Extra headers merged last. */
  extra?: HeadersInit;
};

function readAuthToken(): string | null {
  return localStorage.getItem("authToken");
}

/** Build request headers with optional Bearer token. */
export function authHeaders(options: AuthHeadersOptions = {}): Record<string, string> {
  const headers: Record<string, string> = {};
  if (options.json !== false) {
    headers["Content-Type"] = "application/json";
  }
  const token = readAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (options.extra) {
    const extra = new Headers(options.extra);
    extra.forEach((value, key) => {
      headers[key] = value;
    });
  }
  return headers;
}

/** Clear local auth and notify App to return to the login screen. */
export function invalidateSession(reason = "token_invalid"): void {
  clearAuth();
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent(AUTH_EXPIRED_EVENT, {
        detail: { reason },
      }),
    );
  }
}

export function subscribeAuthExpired(handler: (reason: string) => void): () => void {
  if (typeof window === "undefined") {
    return () => undefined;
  }
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<{ reason?: string }>).detail;
    handler(detail?.reason || "token_invalid");
  };
  window.addEventListener(AUTH_EXPIRED_EVENT, listener);
  return () => window.removeEventListener(AUTH_EXPIRED_EVENT, listener);
}

function extractAuthDetail(payload: unknown): string {
  if (!payload || typeof payload !== "object") {
    return "token_invalid";
  }
  const body = payload as Record<string, unknown>;
  // Canonical body: {code,message,detail} either top-level or under FastAPI "detail".
  const nested =
    body.detail && typeof body.detail === "object"
      ? (body.detail as Record<string, unknown>)
      : null;
  const source = nested ?? body;
  const detail = source.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }
  if (typeof body.detail === "string" && body.detail.trim()) {
    // Legacy FastAPI string detail.
    const legacy = body.detail.toLowerCase();
    if (legacy.includes("expired")) return "token_expired";
    if (legacy.includes("required") || legacy.includes("missing")) return "token_missing";
    return "token_invalid";
  }
  return "token_invalid";
}

async function readJsonSafe(response: Response): Promise<unknown> {
  try {
    return await response.clone().json();
  } catch {
    return null;
  }
}

/**
 * Handle a non-OK response. 401 always invalidates the session and throws AuthError.
 * Other statuses throw a plain Error with a stable message.
 */
export async function handleHttpError(
  response: Response,
  fallbackMessage: string,
): Promise<never> {
  if (response.status === 401) {
    const payload = await readJsonSafe(response);
    const reason = extractAuthDetail(payload);
    invalidateSession(reason);
    throw new AuthError(401, reason);
  }
  throw new Error(`${fallbackMessage} (HTTP ${response.status})`);
}

/**
 * fetch wrapper that attaches auth headers and enforces the 401 session lifecycle.
 * Callers that need the body stream (SSE) still receive the raw Response on success.
 */
export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
  options: { json?: boolean; errorMessage?: string } = {},
): Promise<Response> {
  const headers = authHeaders({
    json: options.json,
    extra: init.headers,
  });
  const response = await fetch(input, {
    ...init,
    headers,
  });
  if (!response.ok) {
    await handleHttpError(
      response,
      options.errorMessage || `Request failed with HTTP ${response.status}`,
    );
  }
  return response;
}
