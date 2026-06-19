import { afterEach, describe, expect, it, vi } from "vitest";

import { parseSseFrame, streamAgent, translateBackendEvent } from "../../api/agentStream";

function sseResponse(frames: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const frame of frames) {
        controller.enqueue(encoder.encode(frame));
      }
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function frame(payload: unknown): string {
  return `event: message\r\ndata: ${JSON.stringify(payload)}\r\n\r\n`;
}

describe("parseSseFrame", () => {
  it("extracts the JSON payload from a data line", () => {
    expect(parseSseFrame('event: message\r\ndata: {"type":"content","data":"hi"}')).toEqual({
      type: "content",
      data: "hi",
    });
  });

  it("returns null for frames without data", () => {
    expect(parseSseFrame(": ping")).toBeNull();
  });
});

describe("translateBackendEvent", () => {
  it("maps route_event to route_selected", () => {
    expect(
      translateBackendEvent({ type: "route_event", route: "metric", summary: "matched" }, "auto"),
    ).toEqual({ type: "route_selected", route: "metric", reason: "matched", mode: "auto" });
  });

  it("passes timeline events through", () => {
    const ev = { type: "tool_event", agent: "log_expert", tool: "logs", status: "completed" };
    expect(translateBackendEvent(ev, "auto")).toBe(ev);
  });
});

describe("streamAgent", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("sends the session owner header required by the assistant API", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    const fetchMock = vi.fn(async () =>
      sseResponse([frame({ type: "complete", route: "metric", answer: "ok", case_id: "", events: [] })]),
    );
    vi.stubGlobal("fetch", fetchMock);

    await streamAgent({ sessionId: "s1", message: "checkout-api slow", mode: "auto", onEvent: vi.fn() });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/assistant",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Session-Owner": "owner-a" }),
      }),
    );
  });

  it("emits route and complete events from the SSE stream", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    const onEvent = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          frame({ type: "route_event", route: "diagnosis", summary: "incident symptoms" }),
          frame({
            type: "agent_event",
            agent: "diagnosis",
            stage: "complete",
            status: "completed",
            summary: "done",
          }),
          frame({
            type: "complete",
            route: "diagnosis",
            answer: "diagnosis report",
            case_id: "case-1",
            events: [],
          }),
        ]),
      ),
    );

    await streamAgent({ sessionId: "s1", message: "checkout-api slow", mode: "auto", onEvent });

    expect(onEvent).toHaveBeenCalledWith({
      type: "route_selected",
      route: "diagnosis",
      reason: "incident symptoms",
      mode: "auto",
    });
    expect(onEvent).toHaveBeenCalledWith({
      type: "complete",
      route: "diagnosis",
      answer: "diagnosis report",
      case_id: "case-1",
      events: [],
    });
  });
});
