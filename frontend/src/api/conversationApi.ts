import type { TimelineEvent } from "../types/events";
import { apiFetch } from "./httpClient";

export type ConversationSummary = {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turn_count: number;
};

export type ConversationTurn = {
  turn_index: number;
  user_message: string;
  assistant_answer: string;
  route: string;
  case_id: string;
  events: TimelineEvent[];
  created_at: string;
};

/** List the caller's past conversations, most-recently-updated first. */
export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await apiFetch("/api/conversations", undefined, {
    errorMessage: "List conversations failed",
  });
  const json = (await response.json().catch(() => null)) as
    | { data?: ConversationSummary[] }
    | null;
  return json?.data ?? [];
}

/** Fetch all turns of one conversation so the UI can restore the full thread. */
export async function getConversation(sessionId: string): Promise<ConversationTurn[]> {
  const response = await apiFetch(`/api/conversations/${encodeURIComponent(sessionId)}`, undefined, {
    errorMessage: "Get conversation failed",
  });
  const json = (await response.json().catch(() => null)) as
    | { data?: { turns?: ConversationTurn[] } }
    | null;
  return json?.data?.turns ?? [];
}

export async function deleteConversation(sessionId: string): Promise<void> {
  const response = await apiFetch(
    `/api/conversations/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
    { errorMessage: "Delete conversation failed" },
  );
  // Success body is ignored; 401 already escalated via apiFetch.
  void response;
}
