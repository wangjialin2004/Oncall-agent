import { AuthError } from "./authApi";
import { apiFetch } from "./httpClient";

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
 * Soft-fails on network/5xx so the UI stays quiet. Auth failures (401) still
 * escalate so the session lifecycle can force re-login.
 */
export async function getCheckpoint(sessionId: string): Promise<CheckpointSummary> {
  const fallback: CheckpointSummary = {
    sessionId,
    enabled: false,
    resumable: false,
  };
  try {
    const response = await apiFetch(
      `/api/checkpoint/${encodeURIComponent(sessionId)}`,
      undefined,
      { errorMessage: "Get checkpoint failed" },
    );
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
  } catch (error) {
    if (error instanceof AuthError) {
      throw error;
    }
    return fallback;
  }
}

/**
 * Drop the checkpoint for this session so the next ``POST /api/assistant``
 * starts fresh instead of resuming. Best-effort for non-auth errors.
 */
export async function deleteCheckpoint(sessionId: string): Promise<number> {
  try {
    const response = await apiFetch(
      `/api/checkpoint/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
      { errorMessage: "Delete checkpoint failed" },
    );
    const json = (await response.json().catch(() => null)) as
      | { data?: { deleted?: number } }
      | null;
    return Number(json?.data?.deleted ?? 0);
  } catch (error) {
    if (error instanceof AuthError) {
      throw error;
    }
    return 0;
  }
}
