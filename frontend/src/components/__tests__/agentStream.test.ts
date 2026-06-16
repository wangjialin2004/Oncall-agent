import { describe, expect, it } from "vitest";

import { parseSseChunk } from "../../api/agentStream";

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
});
