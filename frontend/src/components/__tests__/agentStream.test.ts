import { describe, expect, it, vi } from "vitest";

import { parseSseChunk, streamAgent } from "../../api/agentStream";

describe("parseSseChunk", () => {
  it("parses multiple SSE message frames", () => {
    const events = parseSseChunk(
      'event: message\ndata: {"type":"route_selected","route":"rag","reason":"explicit_mode","mode":"rag"}\n\n' +
        'event: message\ndata: {"type":"content","data":"hello"}\n\n',
    );

    expect(events).toEqual([
      { type: "route_selected", route: "rag", reason: "explicit_mode", mode: "rag" },
      { type: "content", data: "hello" },
    ]);
  });

  it("ignores empty frames", () => {
    expect(parseSseChunk("\n\n")).toEqual([]);
  });

  it("parses CRLF-delimited SSE frames from EventSourceResponse", () => {
    const events = parseSseChunk(
      'event: message\r\ndata: {"type":"route_selected","route":"oncall","reason":"explicit_mode","mode":"oncall"}\r\n\r\n',
    );

    expect(events).toEqual([
      { type: "route_selected", route: "oncall", reason: "explicit_mode", mode: "oncall" },
    ]);
  });

  it("emits CRLF-delimited events while the stream is still open", async () => {
    let controllerRef!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controllerRef = controller;
      },
    });
    const onEvent = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(stream, { status: 200 })),
    );

    const promise = streamAgent({
      sessionId: "s1",
      message: "checkout-api slow",
      mode: "oncall",
      onEvent,
    });

    controllerRef.enqueue(
      new TextEncoder().encode(
        'event: message\r\ndata: {"type":"route_selected","route":"oncall","reason":"explicit_mode","mode":"oncall"}\r\n\r\n',
      ),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(onEvent).toHaveBeenCalledWith({
      type: "route_selected",
      route: "oncall",
      reason: "explicit_mode",
      mode: "oncall",
    });

    controllerRef.close();
    await promise;
    vi.unstubAllGlobals();
  });
});
