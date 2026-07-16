/**
 * 认证 API 接口层
 *
 * 后端接口约定：
 *   POST /api/auth/login
 *   Body:    { username: string, password: string }
 *   成功:    { code: 200, data: { token: string, username: string } }
 *   失败:    { code: 401, message: string }
 *
 *   GET  /api/auth/me
 *   Headers: Authorization: Bearer <token>
 *   成功:    { code: 200, data: { username: string, owner_key: string } }
 *
 *   POST /api/auth/logout
 *   Headers: Authorization: Bearer <token>
 *   成功:    { code: 200 }
 */

export type LoginResult = {
  token: string;
  username: string;
};

export type MeResult = {
  username: string;
  ownerKey: string;
};

export class AuthError extends Error {
  constructor(
    public readonly code: number,
    message: string,
  ) {
    super(message);
    this.name = "AuthError";
  }
}

function loginErrorMessage(payload: unknown, status: number): string {
  if (!payload || typeof payload !== "object") {
    return status === 401 ? "用户名或密码错误" : `登录失败（HTTP ${status}）`;
  }
  const body = payload as Record<string, unknown>;
  const detail = body.detail;
  const message = typeof body.message === "string" ? body.message : "";
  const detailText =
    typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string"
        ? String((detail as { message: string }).message)
        : "";
  const raw = (detailText || message || "").toLowerCase();
  if (raw.includes("invalid username or password") || raw.includes("unauthorized")) {
    return "用户名或密码错误";
  }
  if (raw.includes("required")) {
    return "请输入用户名和密码";
  }
  if (detailText) {
    return detailText;
  }
  if (message) {
    return message;
  }
  return status === 401 ? "用户名或密码错误" : `登录失败（HTTP ${status}）`;
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  const json = await response.json().catch(() => null);

  if (!response.ok || !json) {
    throw new AuthError(response.status, loginErrorMessage(json, response.status));
  }

  if (json.code !== 200) {
    throw new AuthError(json.code ?? response.status, loginErrorMessage(json, response.status));
  }

  const { token, username: returnedUsername } = json.data as LoginResult;
  return { token, username: returnedUsername ?? username };
}

/** Probe whether the stored access token is still accepted by the backend. */
export async function fetchMe(): Promise<MeResult> {
  // Lazy import to avoid a circular dependency at module load time.
  const { apiFetch } = await import("./httpClient");
  const response = await apiFetch("/api/auth/me", { method: "GET" }, {
    errorMessage: "Session probe failed",
  });
  const json = (await response.json().catch(() => null)) as
    | { data?: { username?: string; owner_key?: string } }
    | null;
  const username = String(json?.data?.username ?? "").trim();
  const ownerKey = String(json?.data?.owner_key ?? "").trim();
  if (!username) {
    throw new AuthError(401, "token_invalid");
  }
  return { username, ownerKey };
}

export async function logout(token: string): Promise<void> {
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
      },
      body: JSON.stringify({}),
    });
  } catch {
    // 静默失败：后端接口未就绪时不阻断前端登出
  }
}

export const AUTH_TOKEN_KEY = "authToken";
export const AUTH_USER_KEY = "authUser";

export function saveAuth(token: string, username: string): void {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
  localStorage.setItem(AUTH_USER_KEY, username);
}

export function loadAuth(): { token: string; username: string } | null {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const username = localStorage.getItem(AUTH_USER_KEY);
  if (!token || !username) return null;
  return { token, username };
}

export function clearAuth(): void {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_USER_KEY);
}
