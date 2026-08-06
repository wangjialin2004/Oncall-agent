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

const SAFE_TIMELINE_PAYLOAD_KEYS = new Set([
  "experts",
  "delegated_expert",
  "parallel",
  "wall_ms",
  "step",
  "results",
  "tool_call_id",
  "parent_tool_call_id",
  "tool",
  "resumed_from_step",
  "replayed_steps",
  "started_at",
  "conservative",
  "replay_override",
  "todos",
  "required_evidence",
  "gaps",
  "failed_tools",
  "trigger",
  "focus_route",
  "evidence_count",
  "failed_evidence_count",
  "confidence",
  "result_preview",
  "result_fields",
  "result_items",
  "metric_name",
  "retrieval_type",
  "interval",
  "series_count",
  "duration_ms",
  "data_points_count",
  "note",
]);

const SECRET_PATTERN = /\b(token|api[_-]?key|secret|password|authorization|cookie|access[_-]?key)\b\s*[:=]\s*([^\s,;]+)/gi;
const EMAIL_PATTERN = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g;
const PHONE_PATTERN = /\b1[3-9]\d{9}\b/g;
const NUMERIC_RESULT_FIELD_LABELS = new Set([
  "count",
  "total",
  "series_count",
  "data_points_count",
  "duration_ms",
  "statistics.avg",
  "statistics.max",
  "statistics.min",
  "statistics.p95",
  "alert_info.threshold",
  "cpu.usage_percent",
  "cpu.count",
  "memory.usage_percent",
  "memory.total_bytes",
  "memory.used_bytes",
  "memory.available_bytes",
  "disk.usage_percent",
  "disk.total_bytes",
  "disk.used_bytes",
  "disk.free_bytes",
]);

function safeDetailText(value: unknown, limit = 640): string {
  if (typeof value !== "string") return "";
  return value
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, " ")
    .replace(SECRET_PATTERN, (_whole, name: string) => `${name}=[REDACTED]`)
    .replace(EMAIL_PATTERN, "[REDACTED_EMAIL]")
    .replace(PHONE_PATTERN, "[REDACTED_PHONE]")
    .trim()
    .slice(0, limit);
}

function safeDetailList(value: unknown, limit = 8, itemLimit = 280): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .slice(0, limit)
    .map((item) => safeDetailText(item, itemLimit))
    .filter(Boolean);
}

function safeResultFieldValue(label: string, value: unknown): string {
  if (NUMERIC_RESULT_FIELD_LABELS.has(label) && typeof value === "string") {
    const numeric = value.trim().slice(0, 280);
    if (/^-?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?$/i.test(numeric)) {
      return numeric;
    }
  }
  return safeDetailText(value, 280);
}

function sanitizePublicDetailPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const sanitized = { ...payload };
  for (const key of ["todos", "required_evidence", "gaps", "failed_tools", "result_items"] as const) {
    if (key in sanitized) {
      sanitized[key] = safeDetailList(sanitized[key]);
    }
  }
  for (const key of ["trigger", "focus_route", "confidence", "result_preview", "metric_name", "retrieval_type", "interval", "note"] as const) {
    if (key in sanitized) {
      sanitized[key] = safeDetailText(sanitized[key]);
    }
  }
  for (const key of ["evidence_count", "failed_evidence_count", "series_count", "duration_ms", "data_points_count"] as const) {
    if (typeof sanitized[key] !== "number" || !Number.isFinite(sanitized[key])) {
      delete sanitized[key];
    }
  }
  if (Array.isArray(sanitized.result_fields)) {
    sanitized.result_fields = sanitized.result_fields
      .slice(0, 12)
      .flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const row = item as Record<string, unknown>;
        const label = safeDetailText(row.label, 60);
        const value = safeResultFieldValue(label, row.value);
        return label && value ? [{ label, value }] : [];
      });
  }
  return sanitized;
}

function sanitizeTimelineEvent(payload: Record<string, unknown>): TimelineEvent {
  const privateTopLevel = ["evidence_id", "trace_id", "span_id", "usage"];
  const inner = payload.payload;
  const hasPrivateTopLevel = privateTopLevel.some((key) => key in payload);
  const hasPublicDetails =
    Boolean(inner) &&
    typeof inner === "object" &&
    Object.keys(inner as Record<string, unknown>).some((key) =>
      [
        "todos",
        "required_evidence",
        "gaps",
        "failed_tools",
        "trigger",
        "focus_route",
        "evidence_count",
        "failed_evidence_count",
        "confidence",
        "result_preview",
        "result_fields",
        "result_items",
      ].includes(key),
    );
  const hasUnsafePayload =
    Boolean(inner) &&
    typeof inner === "object" &&
    Object.keys(inner as Record<string, unknown>).some(
      (key) => !SAFE_TIMELINE_PAYLOAD_KEYS.has(key),
    );
  if (!hasPrivateTopLevel && !hasUnsafePayload && !hasPublicDetails) {
    return payload as unknown as TimelineEvent;
  }

  const sanitized = { ...payload };
  for (const key of privateTopLevel) delete sanitized[key];
  if (inner && typeof inner === "object") {
    sanitized.payload = sanitizePublicDetailPayload(Object.fromEntries(
      Object.entries(inner as Record<string, unknown>).filter(([key]) =>
        SAFE_TIMELINE_PAYLOAD_KEYS.has(key),
      ),
    ));
  }
  return sanitized as unknown as TimelineEvent;
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
      const timelineEvent = sanitizeTimelineEvent(payload);
      return {
        type: "route_selected",
        route,
        reason: String(timelineEvent.summary ?? ""),
        mode,
        timelineEvent,
      };
    case "agent_event":
      return translateAgentEvent(payload) ?? sanitizeTimelineEvent(payload);
    case "tool_event":
    case "decision_event":
      return sanitizeTimelineEvent(payload);
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
      const replacementMarker = payload.replace_streamed_answer === true;
      return {
        type: "complete",
        route,
        answer: String(payload.answer ?? ""),
        ...(replacementMarker ? { replace_streamed_answer: true } : {}),
        case_id: String(payload.case_id ?? ""),
        events: Array.isArray(payload.events)
          ? payload.events
              .filter(
                (item): item is Record<string, unknown> =>
                  Boolean(item) && typeof item === "object",
              )
              .map(sanitizeTimelineEvent)
          : [],
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
