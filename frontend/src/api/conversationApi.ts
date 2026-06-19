import type { TimelineEvent } from "../types/events";
import { getSessionOwnerToken } from "./agentStream";

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

function authHeaders(): Record<string, string> {
  const authToken = localStorage.getItem("authToken");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Session-Owner": getSessionOwnerToken(),
  };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }
  return headers;
}

/** List the caller's past conversations, most-recently-updated first. */
export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await fetch("/api/conversations", { headers: authHeaders() });
  if (!response.ok) {
    throw new Error(`List conversations failed with HTTP ${response.status}`);
  }
  const json = (await response.json().catch(() => null)) as
    | { data?: ConversationSummary[] }
    | null;
  return json?.data ?? [];
}

/** Fetch all turns of one conversation so the UI can restore the full thread. */
export async function getConversation(sessionId: string): Promise<ConversationTurn[]> {
  const response = await fetch(`/api/conversations/${encodeURIComponent(sessionId)}`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Get conversation failed with HTTP ${response.status}`);
  }
  const json = (await response.json().catch(() => null)) as
    | { data?: { turns?: ConversationTurn[] } }
    | null;
  return json?.data?.turns ?? [];
}

export async function deleteConversation(sessionId: string): Promise<void> {
  const response = await fetch(`/api/conversations/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Delete conversation failed with HTTP ${response.status}`);
  }
}
