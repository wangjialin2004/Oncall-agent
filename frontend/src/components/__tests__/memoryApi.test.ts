import { afterEach, describe, expect, it, vi } from "vitest";

import { submitFeedback } from "../../api/memoryApi";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("submitFeedback", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("posts a strong adoption with the session owner header", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ data: { experience_id: "exp-1" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const id = await submitFeedback({
      sessionId: "s1",
      userMessage: "checkout-api slow",
      assistantAnswer: "db pool exhausted",
      events: [{ type: "tool_event", evidence_id: "metric-1", summary: "p95 high" }],
      acceptanceLevel: "strong",
      actualRootCause: "db pool exhausted",
    });

    expect(id).toBe("exp-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/feedback");
    expect(init?.headers).toMatchObject({ "X-Session-Owner": "owner-a" });
    const body = JSON.parse(init?.body as string);
    expect(body.acceptance_level).toBe("strong");
    expect(body.user_accepted).toBe(true);
    expect(body.actual_root_cause).toBe("db pool exhausted");
    expect(body.events).toHaveLength(1);
  });

  it("posts a weak acceptance with user_accepted false", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ data: { experience_id: "exp-weak" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await submitFeedback({
      sessionId: "s1",
      userMessage: "checkout-api slow",
      assistantAnswer: "db pool",
      events: [],
      acceptanceLevel: "weak",
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body.acceptance_level).toBe("weak");
    expect(body.user_accepted).toBe(false);
  });

  it("throws on a non-ok response", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));

    await expect(
      submitFeedback({
        sessionId: "s1",
        userMessage: "x",
        assistantAnswer: "y",
        events: [],
        acceptanceLevel: "strong",
      }),
    ).rejects.toThrow(/HTTP 500/);
  });
});
