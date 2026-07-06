import { getSessionOwnerToken } from "./agentStream";

export type CheckpointSummary = {
  sessionId: string;
  /** False when the feature flag stack says checkpointing is off. */
  enabled: boolean;
  /** True when a non-terminal checkpoint exists for this session. */
  resumable: boolean;
  step?: number;
  route?: string;
  startedAt?: string;
  lastStepAt?: string;
  completed?: boolean;
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

type RawCheckpointPayload = {
  session_id?: string;
  enabled?: boolean;
  resumable?: boolean;
  step?: number;
  route?: string;
  started_at?: string;
  last_step_at?: string;
  completed?: boolean;
};

/**
 * Ask the backend whether this session has a resumable checkpoint.
 *
 * Fails soft: any error returns a summary with ``enabled: false`` and
 * ``resumable: false`` so the UI can render without surfacing 5xx noise.
 */
export async function getCheckpoint(sessionId: string): Promise<CheckpointSummary> {
  const fallback: CheckpointSummary = {
    sessionId,
    enabled: false,
    resumable: false,
  };
  try {
    const response = await fetch(`/api/checkpoint/${encodeURIComponent(sessionId)}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      return fallback;
    }
    const json = (await response.json().catch(() => null)) as
      | { data?: RawCheckpointPayload }
      | null;
    const data = json?.data ?? {};
    return {
      sessionId,
      enabled: Boolean(data.enabled),
      resumable: Boolean(data.resumable),
      step: typeof data.step === "number" ? data.step : undefined,
      route: data.route || undefined,
      startedAt: data.started_at || undefined,
      lastStepAt: data.last_step_at || undefined,
      completed: typeof data.completed === "boolean" ? data.completed : undefined,
    };
  } catch {
    return fallback;
  }
}

/**
 * Drop the checkpoint for this session so the next ``POST /api/assistant``
 * starts fresh instead of resuming. Best-effort; errors are swallowed.
 */
export async function deleteCheckpoint(sessionId: string): Promise<number> {
  try {
    const response = await fetch(`/api/checkpoint/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (!response.ok) {
      return 0;
    }
    const json = (await response.json().catch(() => null)) as
      | { data?: { deleted?: number } }
      | null;
    return Number(json?.data?.deleted ?? 0);
  } catch {
    return 0;
  }
}