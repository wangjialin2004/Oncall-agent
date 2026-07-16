import type { TimelineEvent } from "../types/events";
import { apiFetch } from "./httpClient";

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
  const response = await apiFetch(
    "/api/memory/feedback",
    {
      method: "POST",
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
    },
    { errorMessage: "Feedback failed" },
  );

  const json = (await response.json().catch(() => null)) as
    | { data?: { experience_id?: string } }
    | null;
  return String(json?.data?.experience_id ?? "");
}

/** Confirm a pending auto-distill draft (W10). Does not execute remediations. */
export async function confirmDistillDraft(experienceId: string, note = ""): Promise<string> {
  const response = await apiFetch(
    "/api/memory/distill/confirm",
    {
      method: "POST",
      body: JSON.stringify({
        ExperienceId: experienceId,
        Note: note,
      }),
    },
    { errorMessage: "Confirm distill failed" },
  );
  const json = (await response.json().catch(() => null)) as
    | { data?: { experience_id?: string } }
    | null;
  return String(json?.data?.experience_id ?? experienceId);
}

/** Reject a pending auto-distill draft (W10). */
export async function rejectDistillDraft(experienceId: string, note = ""): Promise<string> {
  const response = await apiFetch(
    "/api/memory/distill/reject",
    {
      method: "POST",
      body: JSON.stringify({
        ExperienceId: experienceId,
        Note: note,
      }),
    },
    { errorMessage: "Reject distill failed" },
  );
  const json = (await response.json().catch(() => null)) as
    | { data?: { experience_id?: string } }
    | null;
  return String(json?.data?.experience_id ?? experienceId);
}
