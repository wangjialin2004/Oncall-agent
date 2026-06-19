import type { AgentMode, AgentRoute, AgentStreamEvent, TimelineEvent } from "../types/events";

export type StreamAgentArgs = {
  sessionId: string;
  message: string;
  mode: AgentMode;
  signal?: AbortSignal;
  onEvent: (event: AgentStreamEvent) => void;
};

const SESSION_OWNER_STORAGE_KEY = "sessionOwnerToken";

export function getSessionOwnerToken(): string {
  const existing = localStorage.getItem(SESSION_OWNER_STORAGE_KEY);
  if (existing) {
    return existing;
  }

  const token = `owner-${crypto.randomUUID()}`;
  localStorage.setItem(SESSION_OWNER_STORAGE_KEY, token);
  return token;
}

/** Translate one backend SSE payload into the frontend event union. */
export function translateBackendEvent(
  payload: Record<string, unknown>,
  mode: AgentMode,
): AgentStreamEvent | null {
  const type = String(payload.type ?? "");
  const route = (payload.route ? String(payload.route) : "unknown") as AgentRoute;
  switch (type) {
    case "route_event":
      return {
        type: "route_selected",
        route,
        reason: String(payload.summary ?? ""),
        mode,
      };
    case "agent_event":
    case "tool_event":
    case "decision_event":
      return payload as unknown as TimelineEvent;
    case "content":
      return { type: "content", data: String(payload.data ?? "") };
    case "report":
      return {
        type: "report",
        route,
        case_id: String(payload.case_id ?? ""),
        report: String(payload.report ?? ""),
      };
    case "complete":
      return {
        type: "complete",
        route,
        answer: String(payload.answer ?? ""),
        case_id: String(payload.case_id ?? ""),
        events: (payload.events as TimelineEvent[]) ?? [],
      };
    case "error":
      return {
        type: "error",
        route,
        message: String(payload.message ?? "请求失败"),
        case_id: payload.case_id ? String(payload.case_id) : undefined,
      };
    default:
      return null;
  }
}

/** Parse the `data:` lines out of a single SSE frame. */
export function parseSseFrame(frame: string): Record<string, unknown> | null {
  const dataLines = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());
  if (dataLines.length === 0) {
    return null;
  }
  try {
    return JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function streamAgent(args: StreamAgentArgs): Promise<void> {
  const authToken = localStorage.getItem("authToken");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    "X-Session-Owner": getSessionOwnerToken(),
  };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const response = await fetch("/api/assistant", {
    method: "POST",
    headers,
    body: JSON.stringify({
      Id: args.sessionId,
      Question: args.message,
    }),
    signal: args.signal,
  });

  if (!response.ok) {
    throw new Error(`Agent stream failed with HTTP ${response.status}`);
  }

  if (!response.body) {
    throw new Error("Agent stream returned an empty body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (frame: string) => {
    const payload = parseSseFrame(frame);
    if (!payload) {
      return;
    }
    const event = translateBackendEvent(payload, args.mode);
    if (event) {
      args.onEvent(event);
    }
  };

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex >= 0) {
      const frame = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      dispatch(frame);
      separatorIndex = buffer.indexOf("\n\n");
    }
  }

  if (buffer.trim().length > 0) {
    dispatch(buffer);
  }
}
