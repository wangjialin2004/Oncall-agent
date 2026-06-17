/**
 * 认证 API 接口层
 *
 * 后端接口约定：
 *   POST /api/auth/login
 *   Body:    { username: string, password: string }
 *   成功:    { code: 200, data: { token: string, username: string } }
 *   失败:    { code: 401, message: string }
 *
 *   POST /api/auth/logout   (可选)
 *   Headers: Authorization: Bearer <token>
 *   成功:    { code: 200 }
 */

export type LoginResult = {
  token: string;
  username: string;
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

export async function login(username: string, password: string): Promise<LoginResult> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  const json = await response.json().catch(() => null);

  if (!response.ok || !json) {
    const message = json?.message || `HTTP ${response.status}`;
    throw new AuthError(response.status, message);
  }

  if (json.code !== 200) {
    throw new AuthError(json.code, json.message || "登录失败");
  }

  const { token, username: returnedUsername } = json.data as LoginResult;
  return { token, username: returnedUsername ?? username };
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

const AUTH_TOKEN_KEY = "authToken";
const AUTH_USER_KEY = "authUser";

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
