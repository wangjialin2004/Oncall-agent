import type {
  AgentMode,
  AgentRoute,
  AgentStreamEvent,
  TimelineEvent,
} from "../types/events";
import { apiFetch } from "./httpClient";

export type StreamAgentArgs = {
  sessionId: string;
  message: string;
  mode: AgentMode;
  attachmentIds?: string[];
  signal?: AbortSignal;
  onEvent: (event: AgentStreamEvent) => void;
  /**
   * When ``true``, the backend replays non-whitelisted tool calls from the
   * checkpoint; ``false`` forces conservative close even if the server default
   * would have replayed. ``undefined`` lets the backend apply its own default.
   */
  checkpointReplay?: boolean;
};

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
        timelineEvent: payload as unknown as TimelineEvent,
      };
    case "agent_event":
      return translateAgentEvent(payload) ?? (payload as unknown as TimelineEvent);
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
    case "complete": {
      const distill = payload.distill_draft as Record<string, unknown> | null | undefined;
      const clarification = payload.clarification as Record<string, unknown> | null | undefined;
      const missing = payload.missing_params;
      const suggestedRaw = payload.suggested_actions;
      const escalationRaw = payload.escalation as Record<string, unknown> | null | undefined;
      const suggested_actions = Array.isArray(suggestedRaw)
        ? suggestedRaw
            .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
            .map((item) => ({
              id: String(item.id ?? ""),
              title: String(item.title ?? ""),
              risk: String(item.risk ?? "low"),
              requires_confirm: item.requires_confirm !== false,
            }))
            .filter((item) => item.id)
        : undefined;
      const escalation =
        escalationRaw && typeof escalationRaw === "object"
          ? {
              configured: Boolean(escalationRaw.configured),
              text: String(escalationRaw.text ?? ""),
              contacts: Array.isArray(escalationRaw.contacts)
                ? (escalationRaw.contacts as unknown[])
                    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
                    .map((item) => ({
                      name: String(item.name ?? ""),
                      channel: String(item.channel ?? ""),
                    }))
                    .filter((item) => item.name)
                : [],
            }
          : null;
      return {
        type: "complete",
        route,
        answer: String(payload.answer ?? ""),
        case_id: String(payload.case_id ?? ""),
        events: (payload.events as TimelineEvent[]) ?? [],
        distill_draft: distill
          ? {
              experience_id: String(distill.experience_id ?? ""),
              status: String(distill.status ?? "pending"),
              enabled: Boolean(distill.enabled),
              requires_confirm: distill.requires_confirm !== false,
            }
          : null,
        missing_params: Array.isArray(missing) ? missing.map((x) => String(x)) : undefined,
        clarification: clarification
          ? {
              missing_params: Array.isArray(clarification.missing_params)
                ? (clarification.missing_params as unknown[]).map((x) => String(x))
                : [],
              defaults: (clarification.defaults as Record<string, string>) ?? {},
              question: clarification.question ? String(clarification.question) : "",
            }
          : null,
        suggested_actions,
        escalation,
      };
    }
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

/**
 * Recognize the harness-loop "checkpoint banner" agent_events. We keep them off
 * the timeline channel so they cannot collide with the deduplication logic and
 * always surface in the panel even when the timeline is otherwise empty.
 */
function translateAgentEvent(
  payload: Record<string, unknown>,
): AgentStreamEvent | null {
  const stage = String(payload.stage ?? "");
  if (stage === "checkpoint_resume") {
    const innerPayload = (payload.payload ?? {}) as Record<string, unknown>;
    const replayOverrideRaw = innerPayload.replay_override;
    return {
      type: "checkpoint_resume",
      resumedFromStep: Number(innerPayload.resumed_from_step ?? 0),
      replayedSteps: Number(innerPayload.replayed_steps ?? 0),
      startedAt: String(innerPayload.started_at ?? ""),
      conservative: Boolean(innerPayload.conservative),
      replayOverride:
        replayOverrideRaw === true
          ? true
          : replayOverrideRaw === false
            ? false
            : null,
    };
  }
  if (stage === "checkpoint_conservative_close") {
    const innerPayload = (payload.payload ?? {}) as Record<string, unknown>;
    return {
      type: "checkpoint_conservative_close",
      step: Number(innerPayload.step ?? 0),
    };
  }
  return null;
}

function normalizeSseLineEndings(value: string, isFinal = false): string {
  const normalized = value.replace(/\r\n/g, "\n");
  return isFinal ? normalized.replace(/\r/g, "\n") : normalized.replace(/\r(?!$)/g, "\n");
}

/** Parse the `data:` lines out of a single SSE frame. */
export function parseSseFrame(frame: string): Record<string, unknown> | null {
  const dataLines = normalizeSseLineEndings(frame, true)
    .split("\n")
    .flatMap((line) => {
      if (line === "data") {
        return [""];
      }
      if (!line.startsWith("data:")) {
        return [];
      }
      const value = line.slice(5);
      return [value.startsWith(" ") ? value.slice(1) : value];
    });
  if (dataLines.length === 0) {
    return null;
  }
  try {
    return JSON.parse(dataLines.join("\n").trim()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function streamAgent(args: StreamAgentArgs): Promise<void> {
  const response = await apiFetch(
    "/api/assistant",
    {
      method: "POST",
      headers: { Accept: "text/event-stream" },
      body: JSON.stringify({
        Id: args.sessionId,
        Question: args.message,
        AttachmentIds: args.attachmentIds ?? [],
        CheckpointReplay: args.checkpointReplay,
      }),
      signal: args.signal,
    },
    { errorMessage: "Agent stream failed" },
  );

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
    buffer = normalizeSseLineEndings(buffer);
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex >= 0) {
      const frame = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      dispatch(frame);
      separatorIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  buffer = normalizeSseLineEndings(buffer, true);
  if (buffer.trim().length > 0) {
    dispatch(buffer);
  }
}
