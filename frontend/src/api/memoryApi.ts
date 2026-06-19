import type { TimelineEvent } from "../types/events";
import { getSessionOwnerToken } from "./agentStream";

export type FeedbackAcceptance = "strong" | "weak";

export type SubmitFeedbackArgs = {
  sessionId: string;
  userMessage: string;
  assistantAnswer: string;
  events: TimelineEvent[];
  /** "strong" = explicit adopt/correct; "weak" = passive acceptance on moving on. */
  acceptanceLevel: FeedbackAcceptance;
  actualRootCause?: string;
  finalResolution?: string;
};

/**
 * Resend the run's events timeline to the long-term memory feedback endpoint so the
 * backend can distill an experience card. The frontend holds the timeline (the active
 * path no longer persists a diagnosis case), so this is the only write path for L1.
 */
export async function submitFeedback(args: SubmitFeedbackArgs): Promise<string> {
  const authToken = localStorage.getItem("authToken");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Session-Owner": getSessionOwnerToken(),
  };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const response = await fetch("/api/memory/feedback", {
    method: "POST",
    headers,
    body: JSON.stringify({
      session_id: args.sessionId,
      user_message: args.userMessage,
      assistant_answer: args.assistantAnswer,
      user_accepted: args.acceptanceLevel === "strong",
      acceptance_level: args.acceptanceLevel,
      actual_root_cause: args.actualRootCause ?? "",
      final_resolution: args.finalResolution ?? "",
      events: args.events,
    }),
  });

  if (!response.ok) {
    throw new Error(`Feedback failed with HTTP ${response.status}`);
  }

  const json = (await response.json().catch(() => null)) as
    | { data?: { experience_id?: string } }
    | null;
  return String(json?.data?.experience_id ?? "");
}
