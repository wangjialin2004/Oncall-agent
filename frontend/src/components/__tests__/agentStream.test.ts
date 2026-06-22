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

function chunkedSseResponse(chunks: Uint8Array[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
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

  it("accepts bare carriage-return separators", () => {
    expect(parseSseFrame('event: message\rdata: {"type":"content","data":"hi"}')).toEqual({
      type: "content",
      data: "hi",
    });
  });
});

describe("translateBackendEvent", () => {
  it("maps route_event to route_selected", () => {
    const payload = { type: "route_event", route: "metric", summary: "matched" };
    expect(
      translateBackendEvent(payload, "auto"),
    ).toEqual({
      type: "route_selected",
      route: "metric",
      reason: "matched",
      mode: "auto",
      timelineEvent: payload,
    });
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
      timelineEvent: { type: "route_event", route: "diagnosis", summary: "incident symptoms" },
    });
    expect(onEvent).toHaveBeenCalledWith({
      type: "complete",
      route: "diagnosis",
      answer: "diagnosis report",
      case_id: "case-1",
      events: [],
    });
  });

  it("emits each event when multiple SSE frames arrive in one network chunk", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    const onEvent = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          [
            frame({ type: "content", data: "first " }),
            frame({ type: "content", data: "second" }),
            frame({ type: "complete", route: "metric", answer: "first second", case_id: "", events: [] }),
          ].join(""),
        ]),
      ),
    );

    await streamAgent({ sessionId: "s1", message: "checkout-api slow", mode: "auto", onEvent });

    expect(onEvent).toHaveBeenNthCalledWith(1, { type: "content", data: "first " });
    expect(onEvent).toHaveBeenNthCalledWith(2, { type: "content", data: "second" });
    expect(onEvent).toHaveBeenNthCalledWith(3, {
      type: "complete",
      route: "metric",
      answer: "first second",
      case_id: "",
      events: [],
    });
  });

  it("keeps streaming when SSE frames and multibyte content are split across tiny chunks", async () => {
    localStorage.setItem("sessionOwnerToken", "owner-a");
    const onEvent = vi.fn();
    const encoder = new TextEncoder();
    const bytes = encoder.encode(
      [
        frame({ type: "content", data: "实时" }),
        frame({ type: "content", data: "输出" }),
        frame({ type: "complete", route: "metric", answer: "实时输出", case_id: "", events: [] }),
      ].join(""),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => chunkedSseResponse([...bytes].map((byte) => Uint8Array.of(byte)))),
    );

    await streamAgent({ sessionId: "s1", message: "checkout-api slow", mode: "auto", onEvent });

    expect(onEvent).toHaveBeenNthCalledWith(1, { type: "content", data: "实时" });
    expect(onEvent).toHaveBeenNthCalledWith(2, { type: "content", data: "输出" });
    expect(onEvent).toHaveBeenNthCalledWith(3, {
      type: "complete",
      route: "metric",
      answer: "实时输出",
      case_id: "",
      events: [],
    });
  });
});
