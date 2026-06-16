import type { AgentMode, AgentStreamEvent } from "../types/events";

export type StreamAgentArgs = {
  sessionId: string;
  message: string;
  mode: AgentMode;
  signal?: AbortSignal;
  onEvent: (event: AgentStreamEvent) => void;
};

function normalizeSseNewlines(chunk: string): string {
  return chunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

export function parseSseChunk(chunk: string): AgentStreamEvent[] {
  return normalizeSseNewlines(chunk)
    .split("\n\n")
    .map((frame) => frame.trim())
    .filter(Boolean)
    .map((frame) => {
      const dataLine = frame.split("\n").find((line) => line.startsWith("data:"));
      if (!dataLine) {
        return null;
      }
      return JSON.parse(dataLine.slice("data:".length).trim()) as AgentStreamEvent;
    })
    .filter((event): event is AgentStreamEvent => event !== null);
}

export async function streamAgent(args: StreamAgentArgs): Promise<void> {
  const response = await fetch("/api/agent/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      session_id: args.sessionId,
      message: args.message,
      mode: args.mode,
    }),
    signal: args.signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Agent stream failed with HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    buffer = normalizeSseNewlines(buffer);
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const event of parseSseChunk(frames.join("\n\n"))) {
      args.onEvent(event);
    }
  }

  for (const event of parseSseChunk(buffer)) {
    args.onEvent(event);
  }
}
