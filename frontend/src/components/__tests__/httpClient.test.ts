import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthError } from "../../api/authApi";
import {
  AUTH_EXPIRED_EVENT,
  apiFetch,
  authHeaders,
  handleHttpError,
  invalidateSession,
} from "../../api/httpClient";

describe("httpClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("attaches the bearer token from localStorage", () => {
    localStorage.setItem("authToken", "tok-1");
    expect(authHeaders()).toMatchObject({
      "Content-Type": "application/json",
      Authorization: "Bearer tok-1",
    });
  });

  it("invalidates the session and throws AuthError on 401", async () => {
    localStorage.setItem("authToken", "stale");
    localStorage.setItem("authUser", "pilot");
    const events: string[] = [];
    const listener = (event: Event) => {
      events.push((event as CustomEvent).type);
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, listener);

    const response = new Response(
      JSON.stringify({
        detail: { code: 401, message: "unauthorized", detail: "token_expired" },
      }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    );

    await expect(handleHttpError(response, "probe failed")).rejects.toBeInstanceOf(AuthError);
    expect(localStorage.getItem("authToken")).toBeNull();
    expect(localStorage.getItem("authUser")).toBeNull();
    expect(events).toContain(AUTH_EXPIRED_EVENT);

    window.removeEventListener(AUTH_EXPIRED_EVENT, listener);
  });

  it("apiFetch escalates 401 through the shared path", async () => {
    localStorage.setItem("authToken", "stale");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: { code: 401, message: "unauthorized", detail: "token_invalid" } }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(apiFetch("/api/conversations")).rejects.toMatchObject({
      name: "AuthError",
      code: 401,
      message: "token_invalid",
    });
    expect(localStorage.getItem("authToken")).toBeNull();
  });

  it("invalidateSession clears storage even without a network call", () => {
    localStorage.setItem("authToken", "x");
    localStorage.setItem("authUser", "y");
    invalidateSession("token_invalid");
    expect(localStorage.getItem("authToken")).toBeNull();
    expect(localStorage.getItem("authUser")).toBeNull();
  });
});
