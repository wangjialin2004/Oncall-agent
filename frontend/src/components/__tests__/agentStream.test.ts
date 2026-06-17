import { afterEach, describe, expect, it, vi } from "vitest";

import { streamAgent } from "../../api/agentStream";

describe("streamAgent", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("sends the session owner header required by the assistant API", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          code: 200,
          data: {
            success: true,
            route: "rag",
            route_reason: "auto",
            answer: "ok",
            events: [],
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await streamAgent({
      sessionId: "s1",
      message: "checkout-api slow",
      mode: "auto",
      onEvent: vi.fn(),
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/assistant",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-Session-Owner": "owner-a",
        }),
      }),
    );
  });

  it("emits route and complete events from the assistant JSON response", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    const onEvent = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            code: 200,
            data: {
              success: true,
              route: "aiops",
              route_reason: "incident symptoms",
              answer: "diagnosis report",
              case_id: "case-1",
              events: [
                {
                  type: "agent_event",
                  agent: "triage",
                  stage: "triage",
                  status: "completed",
                  summary: "structured incident",
                },
              ],
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await streamAgent({
      sessionId: "s1",
      message: "checkout-api slow",
      mode: "auto",
      onEvent,
    });

    expect(onEvent).toHaveBeenCalledWith({
      type: "route_selected",
      route: "aiops",
      reason: "incident symptoms",
      mode: "auto",
    });
    expect(onEvent).toHaveBeenCalledWith({
      type: "complete",
      route: "aiops",
      answer: "diagnosis report",
      case_id: "case-1",
      events: [
        {
          type: "agent_event",
          agent: "triage",
          stage: "triage",
          status: "completed",
          summary: "structured incident",
        },
      ],
    });
  });
});
