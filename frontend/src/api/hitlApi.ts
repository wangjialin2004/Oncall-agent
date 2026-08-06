import { apiFetch } from "./httpClient";

export type ConfirmSuggestionArgs = {
  sessionId: string;
  actionId: string;
  note?: string;
};

export type ConfirmSuggestionResult = {
  accepted: boolean;
  executed: boolean;
  hint: string;
};

/**
 * Record operator acknowledgement of a *suggested* action.
 * Audit-only: never executes restart/rollback/scale.
 */
export async function confirmSuggestion(
  args: ConfirmSuggestionArgs,
): Promise<ConfirmSuggestionResult> {
  const response = await apiFetch(
    "/api/hitl/confirm-suggestion",
    {
      method: "POST",
      body: JSON.stringify({
        SessionId: args.sessionId,
        ActionId: args.actionId,
        Note: args.note ?? "",
      }),
    },
    { errorMessage: "Confirm suggestion failed" },
  );
  const json = (await response.json().catch(() => null)) as
    | {
        data?: {
          accepted?: boolean;
          executed?: boolean;
          hint?: string;
        };
      }
    | null;
  const data = json?.data ?? {};
  return {
    accepted: data.accepted !== false,
    executed: data.executed === true,
    hint: String(data.hint ?? "确认已记录；系统不会自动执行变更/重启/回滚。"),
  };
}
