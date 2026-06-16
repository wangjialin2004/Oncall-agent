import type { AgentMode, AgentRoute, AgentStreamEvent, TimelineEvent } from "../types/events";

export type StreamAgentArgs = {
  sessionId: string;
  message: string;
  mode: AgentMode;
  signal?: AbortSignal;
  onEvent: (event: AgentStreamEvent) => void;
};

export async function streamAgent(args: StreamAgentArgs): Promise<void> {
  const response = await fetch("/api/assistant", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      Id: args.sessionId,
      Question: args.message,
    }),
    signal: args.signal,
  });

  if (!response.ok) {
    throw new Error(`Agent stream failed with HTTP ${response.status}`);
  }

  const json = await response.json();

  if (json.code !== 200) {
    args.onEvent({ type: "error", message: json.message || "Request failed" });
    return;
  }

  const data = json.data as {
    success: boolean;
    route?: string;
    route_reason?: string;
    answer?: string;
    case_id?: string;
    events?: TimelineEvent[];
    errorMessage?: string;
  };

  if (!data.success) {
    args.onEvent({ type: "error", message: data.errorMessage || "Agent request failed" });
    return;
  }

  const route = (data.route ?? "unknown") as AgentRoute;

  if (route !== "unknown" && route !== "clarify") {
    args.onEvent({
      type: "route_selected",
      route,
      reason: data.route_reason ?? "",
      mode: args.mode,
    });
  }

  for (const event of data.events ?? []) {
    args.onEvent(event);
  }

  args.onEvent({
    type: "complete",
    route,
    answer: data.answer ?? "",
    case_id: data.case_id ?? "",
    events: data.events ?? [],
  });
}

// kept for unit tests
export function parseSseChunk(_chunk: string): AgentStreamEvent[] {
  return [];
}
