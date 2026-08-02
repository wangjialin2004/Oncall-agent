import { useCallback, useEffect, useRef, useState } from "react";

import { AuthError, clearAuth, fetchMe, loadAuth, logout } from "./api/authApi";
import { streamAgent } from "./api/agentStream";
import { type CheckpointSummary, getCheckpoint } from "./api/checkpointApi";
import {
  type ConversationSummary,
  deleteConversation,
  getConversation,
  listConversations,
} from "./api/conversationApi";
import { uploadFile } from "./api/fileApi";
import { subscribeAuthExpired } from "./api/httpClient";
import { confirmSuggestion } from "./api/hitlApi";
import { confirmDistillDraft, rejectDistillDraft, submitFeedback } from "./api/memoryApi";
import { AgentProcessPanel } from "./components/AgentProcessPanel";
import { AppShell } from "./components/AppShell";
import { ChatWorkspace } from "./components/ChatWorkspace";
import { LoginPage } from "./components/LoginPage";
import { ServiceBaselineManager } from "./components/ServiceBaselineManager";
import { Sidebar } from "./components/Sidebar";
import type {
  AgentMode,
  AgentRoute,
  AgentRun,
  AgentStreamEvent,
  ChatMessage,
  TimelineEvent,
} from "./types/events";
import type { PendingAttachment } from "./components/ChatWorkspace";

const SESSION_STORAGE_KEY = "currentSessionId";

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function loadOrCreateSessionId(): string {
  const existing = localStorage.getItem(SESSION_STORAGE_KEY);
  if (existing) {
    return existing;
  }
  const id = createId("session");
  localStorage.setItem(SESSION_STORAGE_KEY, id);
  return id;
}

/** A fresh run record. Runs are keyed by their assistant message id. */
function makeRun(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    runId: "",
    sessionId: "",
    mode: "auto",
    route: "unknown",
    status: "idle",
    events: [],
    answer: "",
    caseId: "",
    error: "",
    userMessage: "",
    feedback: "",
    ...overrides,
  };
}

function normalizeTimelineEvents(events: TimelineEvent[]): TimelineEvent[] {
  const seen = new Set<string>();
  const normalized: TimelineEvent[] = [];
  for (const event of events) {
    const activityId = String(event.payload?.tool_call_id ?? "");
    const parentActivityId = String(event.payload?.parent_tool_call_id ?? "");
    const key = [
      event.type,
      event.span_id,
      event.evidence_id,
      activityId,
      parentActivityId,
      event.agent,
      event.tool,
      event.stage,
      event.status,
      event.summary,
    ]
      .filter(Boolean)
      .join("|");
    if (key && seen.has(key)) {
      continue;
    }
    if (key) {
      seen.add(key);
    }
    normalized.push(event);
  }
  return normalized;
}

/** Passively record a completed-but-un-adopted run as weak acceptance (low confidence). */
function weakAcceptIfNeeded(prev: AgentRun | undefined): void {
  if (!prev || prev.status !== "completed" || !prev.answer || !prev.userMessage || prev.feedback !== "") {
    return;
  }
  void submitFeedback({
    sessionId: prev.sessionId,
    userMessage: prev.userMessage,
    assistantAnswer: prev.answer,
    events: prev.events,
    acceptanceLevel: "weak",
  }).catch(() => {
    // best-effort: passive signal must never disrupt the chat experience
  });
}

type AuthState = { token: string; username: string } | null;

export default function App() {
  const inlineActivityEnabled =
    import.meta.env.VITE_INLINE_AGENT_ACTIVITY_ENABLED !== "false" &&
    import.meta.env.VITE_INLINE_AGENT_ACTIVITY_ENABLED !== "0";
  const granularActivityEnabled =
    import.meta.env.VITE_GRANULAR_AGENT_ACTIVITY_ENABLED !== "false" &&
    import.meta.env.VITE_GRANULAR_AGENT_ACTIVITY_ENABLED !== "0";
  const saved = loadAuth();
  const [auth, setAuth] = useState<AuthState>(saved);
  const [authBootstrapping, setAuthBootstrapping] = useState<boolean>(Boolean(saved));
  const [loginNotice, setLoginNotice] = useState<string | null>(null);

  const [mode, setMode] = useState<AgentMode>("auto");
  const [view, setView] = useState<"chat" | "baseline">("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Per-turn agent process, keyed by the assistant message id of that turn.
  const [runs, setRuns] = useState<Record<string, AgentRun>>({});
  // Which turn's process the side panel shows (defaults to the active/latest turn).
  const [selectedId, setSelectedId] = useState<string>("");
  const [sessions, setSessions] = useState<ConversationSummary[]>([]);
  const [sessionId, setSessionId] = useState<string>(loadOrCreateSessionId);
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [checkpointStatus, setCheckpointStatus] = useState<Record<string, CheckpointSummary>>({});
  // Per-session opt-in for aggressive checkpoint replay on the next send.
  // Reset whenever the active session changes so we never leak the toggle
  // across unrelated threads.
  const [checkpointReplay, setCheckpointReplay] = useState<boolean>(false);
  const abortRef = useRef<AbortController | null>(null);
  // Assistant message id of the turn currently streaming, so events route correctly.
  const activeIdRef = useRef<string>("");

  const forceLogout = useCallback((notice?: string) => {
    abortRef.current?.abort();
    activeIdRef.current = "";
    clearAuth();
    setAuth(null);
    setAuthBootstrapping(false);
    setMessages([]);
    setRuns({});
    setSelectedId("");
    setSessions([]);
    setPendingAttachments([]);
    setCheckpointStatus({});
    setLoginNotice(notice ?? "登录已过期，请重新登录");
  }, []);

  function appendStreamedChunk(assistantId: string, chunk: string) {
    if (!chunk) {
      return;
    }
    setMessages((items) =>
      items.map((item) =>
        item.id === assistantId ? { ...item, content: item.content + chunk } : item,
      ),
    );
  }

  const refreshSessions = useCallback(async () => {
    let summaries: ConversationSummary[] = [];
    try {
      summaries = await listConversations();
      setSessions(summaries);
    } catch (error) {
      if (error instanceof AuthError) {
        // 401 already invalidates the session via httpClient.
        return;
      }
      // best-effort: the chat still works without the history list
      return;
    }
    // Fan out one GET /api/checkpoint per session. Failures degrade to "no
    // checkpoint" silently — the feature is opt-in, so missing data should
    // not break the sidebar. Auth failures still escalate.
    try {
      const updates = await Promise.all(
        summaries.map(async (summary) => [summary.session_id, await getCheckpoint(summary.session_id)] as const),
      );
      const next: Record<string, CheckpointSummary> = {};
      for (const [sid, status] of updates) {
        if (status.enabled) {
          next[sid] = status;
        }
      }
      setCheckpointStatus(next);
    } catch (error) {
      if (error instanceof AuthError) {
        return;
      }
    }
  }, []);

  const loadSession = useCallback(async (sid: string) => {
    abortRef.current?.abort();
    activeIdRef.current = "";
    localStorage.setItem(SESSION_STORAGE_KEY, sid);
    setSessionId(sid);
    setView("chat");
    // Capture the generation token so a slower load cannot clobber a newer
    // session switch or an in-flight send that already claimed activeIdRef.
    const loadToken = sid;
    try {
      const turns = await getConversation(sid);
      if (activeIdRef.current || localStorage.getItem(SESSION_STORAGE_KEY) !== loadToken) {
        return;
      }
      const restoredMessages: ChatMessage[] = [];
      const restoredRuns: Record<string, AgentRun> = {};
      let lastAssistantId = "";
      for (const turn of turns) {
        restoredMessages.push({ id: createId("user"), role: "user", content: turn.user_message });
        const assistantId = createId("assistant");
        restoredMessages.push({
          id: assistantId,
          role: "assistant",
          content: turn.assistant_answer,
          status: "completed",
        });
        const suggestedActions = (turn.events ?? []).find(
          (event) => event.type === "decision_event" && event.stage === "suggested_actions",
        )?.actions;
        restoredRuns[assistantId] = makeRun({
          runId: assistantId,
          sessionId: sid,
          route: (turn.route || "unknown") as AgentRoute,
          status: "completed",
          events: turn.events ?? [],
          answer: turn.assistant_answer,
          caseId: turn.case_id ?? "",
          userMessage: turn.user_message,
          suggestedActions,
        });
        lastAssistantId = assistantId;
      }
      setMessages(restoredMessages);
      setRuns(restoredRuns);
      setSelectedId(lastAssistantId);
      // Refreshing the per-session checkpoint status here keeps the sidebar
      // badge and any "continue" affordances in sync right after switching.
      const status = await getCheckpoint(sid);
      if (activeIdRef.current || localStorage.getItem(SESSION_STORAGE_KEY) !== loadToken) {
        return;
      }
      setCheckpointStatus((current) =>
        status.enabled ? { ...current, [sid]: status } : current,
      );
    } catch (error) {
      if (error instanceof AuthError) {
        return;
      }
      if (activeIdRef.current || localStorage.getItem(SESSION_STORAGE_KEY) !== loadToken) {
        return;
      }
      setMessages([]);
      setRuns({});
      setSelectedId("");
    }
  }, []);

  // Global 401 path: any authenticated request can force re-login.
  useEffect(() => {
    return subscribeAuthExpired(() => {
      forceLogout("登录已过期，请重新登录");
    });
  }, [forceLogout]);

  // On login (and initial mount with a saved session), probe the token then hydrate.
  useEffect(() => {
    if (!auth) {
      setAuthBootstrapping(false);
      return;
    }

    let cancelled = false;
    setAuthBootstrapping(true);
    void (async () => {
      try {
        const me = await fetchMe();
        if (cancelled) {
          return;
        }
        setAuth((current) =>
          current ? { token: current.token, username: me.username || current.username } : current,
        );
        setLoginNotice(null);
        await refreshSessions();
        if (!cancelled) {
          await loadSession(sessionId);
        }
      } catch (error) {
        if (cancelled) {
          return;
        }
        if (error instanceof AuthError) {
          // invalidateSession already cleared storage; forceLogout syncs React state.
          forceLogout("登录已过期，请重新登录");
          return;
        }
        // Network blip on probe: keep the saved session and still try to hydrate.
        void refreshSessions();
        void loadSession(sessionId);
      } finally {
        // Always clear the spinner for this effect generation, even if a
        // React Strict Mode remount cancelled the previous async chain.
        setAuthBootstrapping(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth?.token]);

  function handleLogin(token: string, username: string) {
    setLoginNotice(null);
    setAuth({ token, username });
  }

  async function handleLogout() {
    abortRef.current?.abort();
    weakAcceptIfNeeded(runs[activeIdRef.current]);
    if (auth?.token) {
      await logout(auth.token);
    }
    clearAuth();
    setAuth(null);
    setAuthBootstrapping(false);
    setLoginNotice(null);
    setMessages([]);
    setRuns({});
    setSelectedId("");
    setSessions([]);
    activeIdRef.current = "";
  }

  function applyEvent(event: AgentStreamEvent) {
    const assistantId = activeIdRef.current;
    if (!assistantId) {
      return;
    }

    if (event.type === "content") {
      appendStreamedChunk(assistantId, event.data);
    } else if (event.type === "report") {
      setMessages((items) =>
        items.map((item) => (item.id === assistantId ? { ...item, content: event.report } : item)),
      );
    } else if (event.type === "complete") {
      setMessages((items) =>
        items.map((item) =>
          item.id === assistantId
            ? {
                ...item,
                // Only replace provisional streamed prose when the server
                // explicitly marks its final answer as authoritative. Older
                // servers and the rollback flag retain legacy append output.
                content:
                  event.replace_streamed_answer && event.answer
                    ? event.answer
                    : item.content || event.answer || "",
                status: "completed",
              }
            : item,
        ),
      );
    } else if (event.type === "error") {
      setMessages((items) =>
        items.map((item) =>
          item.id === assistantId
            ? {
                ...item,
                content: item.content ? item.content : "（执行失败）",
                status: "error",
              }
            : item,
        ),
      );
    }

    setRuns((current) => {
      const prev = current[assistantId] ?? makeRun({ runId: assistantId, sessionId });
      let next = prev;
      if (event.type === "route_selected") {
        next = {
          ...prev,
          route: event.route,
          mode: event.mode,
          status: "running",
          events: event.timelineEvent
            ? normalizeTimelineEvents([...prev.events, event.timelineEvent])
            : prev.events,
        };
      } else if (
        event.type === "agent_event" ||
        event.type === "tool_event" ||
        event.type === "decision_event"
      ) {
        const timeline = event as TimelineEvent;
        const payload = (timeline.payload ?? {}) as Record<string, unknown>;
        const stage = String(timeline.stage || "");
        const missingFromPayload = Array.isArray(payload.missing_params)
          ? (payload.missing_params as unknown[]).map((x) => String(x))
          : undefined;
        next = {
          ...prev,
          events: normalizeTimelineEvents([...prev.events, timeline]),
          ...(stage.includes("clarify") && missingFromPayload
            ? {
                missingParams: missingFromPayload,
                clarification: {
                  missing_params: missingFromPayload,
                  defaults: (payload.defaults as Record<string, string>) ?? {},
                  question: payload.question ? String(payload.question) : prev.clarification?.question,
                },
              }
            : {}),
        };
      } else if (event.type === "content") {
        next = { ...prev, answer: `${prev.answer}${event.data}` };
      } else if (event.type === "report") {
        next = { ...prev, caseId: event.case_id, answer: event.report };
      } else if (event.type === "complete") {
        next = {
          ...prev,
          route: event.route,
          status: "completed",
          answer: event.answer,
          caseId: event.case_id,
          events: normalizeTimelineEvents(
            event.events.length > 0 ? [...prev.events, ...event.events] : prev.events,
          ),
          distillDraft: event.distill_draft ?? null,
          missingParams: event.missing_params ?? event.clarification?.missing_params ?? prev.missingParams,
          clarification: event.clarification ?? prev.clarification ?? null,
          suggestedActions: event.suggested_actions ?? prev.suggestedActions ?? [],
        };
      } else if (event.type === "error") {
        next = {
          ...prev,
          status: "error",
          error: event.message,
          caseId: event.case_id ?? prev.caseId,
        };
      } else if (event.type === "checkpoint_resume") {
        next = { ...prev, checkpointResume: event };
      } else if (event.type === "checkpoint_conservative_close") {
        next = { ...prev, checkpointConservativeClose: event };
      }
      return { ...current, [assistantId]: next };
    });

    // When the harness tells us it consumed a checkpoint, refresh the sidebar
    // badge: that session's checkpoint is either gone (completed) or about to
    // be replaced by the new run's first save. Either way, drop the badge.
    if (
      event.type === "checkpoint_resume" ||
      event.type === "checkpoint_conservative_close"
    ) {
      setCheckpointStatus((current) => {
        if (!current[sessionId]) {
          return current;
        }
        const next = { ...current };
        delete next[sessionId];
        return next;
      });
    }
  }

  async function handleSend(message: string) {
    abortRef.current?.abort();
    weakAcceptIfNeeded(runs[activeIdRef.current]);
    const controller = new AbortController();
    abortRef.current = controller;

    const userId = createId("user");
    const assistantId = createId("assistant");
    activeIdRef.current = assistantId;

    setMessages((items) => [
      ...items,
      { id: userId, role: "user", content: message },
      { id: assistantId, role: "assistant", content: "", status: "running" },
    ]);
    setRuns((current) => ({
      ...current,
      [assistantId]: makeRun({
        runId: assistantId,
        sessionId,
        mode,
        status: "running",
        userMessage: message,
      }),
    }));
    setSelectedId(assistantId);
    const currentAttachments = pendingAttachments;
    setPendingAttachments([]);

    try {
      await streamAgent({
        sessionId,
        message,
        mode,
        attachmentIds: currentAttachments.map((item) => item.fileId),
        checkpointReplay,
        signal: controller.signal,
        onEvent: applyEvent,
      });
      // The turn is now persisted backend-side; refresh the sidebar so it appears.
      void refreshSessions();
    } catch (error) {
      if (error instanceof AuthError) {
        // Session expiry is handled by the global auth:expired subscriber.
        return;
      }
      if (!controller.signal.aborted) {
        const errorMessage = error instanceof Error ? error.message : String(error);
        setMessages((items) =>
          items.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  status: "error",
                  content: item.content ? item.content : "（执行失败）",
                }
              : item,
          ),
        );
        setRuns((current) => ({
          ...current,
          [assistantId]: {
            ...(current[assistantId] ?? makeRun({ runId: assistantId, sessionId })),
            status: "error",
            error: errorMessage,
          },
        }));
      }
    }
  }

  async function handleFeedback(
    runId: string,
    kind: "adopted" | "corrected",
    actualRootCause = "",
  ) {
    const current = runs[runId];
    if (!current || !current.answer || !current.userMessage || current.feedback !== "") {
      return;
    }
    setRuns((c) => ({ ...c, [runId]: { ...c[runId], feedback: kind } }));
    try {
      // Prefer confirming an existing auto-distill draft when present.
      const draftId = current.distillDraft?.experience_id;
      if (kind === "adopted" && draftId && current.distillDraft?.status === "pending") {
        await confirmDistillDraft(draftId, "panel-adopt");
        setRuns((c) => ({
          ...c,
          [runId]: { ...c[runId], distillStatus: "confirmed" },
        }));
        return;
      }
      await submitFeedback({
        sessionId: current.sessionId || sessionId,
        userMessage: current.userMessage,
        assistantAnswer: current.answer,
        events: current.events,
        acceptanceLevel: "strong",
        actualRootCause: kind === "corrected" ? actualRootCause : "",
      });
    } catch {
      // best-effort: keep the optimistic UI, the user can retry by re-running
    }
  }

  async function handleDistill(runId: string, action: "confirm" | "reject") {
    const current = runs[runId];
    const draftId = current?.distillDraft?.experience_id;
    if (!current || !draftId || current.distillStatus) {
      return;
    }
    setRuns((c) => ({
      ...c,
      [runId]: {
        ...c[runId],
        distillStatus: action === "confirm" ? "confirmed" : "rejected",
        feedback: action === "confirm" ? "adopted" : c[runId].feedback,
      },
    }));
    try {
      if (action === "confirm") {
        await confirmDistillDraft(draftId, "panel-confirm");
      } else {
        await rejectDistillDraft(draftId, "panel-reject");
      }
    } catch {
      // optimistic UI retained
    }
  }

  async function handleConfirmSuggestion(runId: string, actionId: string) {
    const current = runs[runId];
    if (!current || !actionId) {
      return;
    }
    const already = current.confirmedActionIds ?? [];
    if (already.includes(actionId)) {
      return;
    }
    // Optimistic mark — confirm is audit-only and should feel instant.
    setRuns((c) => ({
      ...c,
      [runId]: {
        ...c[runId],
        confirmedActionIds: [...(c[runId].confirmedActionIds ?? []), actionId],
      },
    }));
    try {
      await confirmSuggestion({
        sessionId: current.sessionId || sessionId,
        actionId,
        note: "panel-confirm",
      });
    } catch {
      // Roll back only this action id so other confirms stay.
      setRuns((c) => ({
        ...c,
        [runId]: {
          ...c[runId],
          confirmedActionIds: (c[runId].confirmedActionIds ?? []).filter((id) => id !== actionId),
        },
      }));
    }
  }

  function handleNewSession() {
    abortRef.current?.abort();
    weakAcceptIfNeeded(runs[activeIdRef.current]);
    const id = createId("session");
    localStorage.setItem(SESSION_STORAGE_KEY, id);
    setSessionId(id);
    setMessages([]);
    setRuns({});
    setPendingAttachments([]);
    setSelectedId("");
    setCheckpointReplay(false);
    activeIdRef.current = "";
    setView("chat");
  }

  async function handleSelectSession(sid: string) {
    if (sid === sessionId) {
      setView("chat");
      return;
    }
    weakAcceptIfNeeded(runs[activeIdRef.current]);
    // Aggressive replay only makes sense for the session it was opted into.
    setCheckpointReplay(false);
    await loadSession(sid);
  }

  async function handleDeleteSession(sid: string) {
    // Optimistically drop the row first so a slow/failed post-delete
    // list refresh cannot leave the sidebar looking like "delete did nothing".
    // The active workspace is only reset after the API succeeds, so a failed
    // delete does not wipe the open thread.
    const previousSessions = sessions;
    const previousCheckpoint = checkpointStatus[sid];
    setSessions((items) => items.filter((session) => session.session_id !== sid));
    setCheckpointStatus((current) => {
      const { [sid]: _removed, ...remaining } = current;
      return remaining;
    });

    try {
      await deleteConversation(sid);
    } catch {
      setSessions(previousSessions);
      if (previousCheckpoint) {
        setCheckpointStatus((current) => ({ ...current, [sid]: previousCheckpoint }));
      }
      return;
    }

    if (sid === sessionId) {
      handleNewSession();
    }
    // Reconcile with the server when possible, but never block the UI on it.
    void refreshSessions();
  }

  function handleStop() {
    abortRef.current?.abort();
    const assistantId = activeIdRef.current;
    if (!assistantId) {
      return;
    }
    setMessages((items) =>
      items.map((item) =>
        item.id === assistantId && item.status === "running"
          ? {
              ...item,
              status: "cancelled",
              content: item.content ? item.content : "（已取消）",
            }
          : item,
      ),
    );
    setRuns((current) =>
      current[assistantId]
        ? { ...current, [assistantId]: { ...current[assistantId], status: "cancelled" } }
        : current,
    );
  }

  async function handleUploadFile(file: File) {
    const uploaded = await uploadFile(file);
    setPendingAttachments((current) => {
      if (current.some((item) => item.fileId === uploaded.fileId)) {
        return current;
      }
      return [...current, { fileId: uploaded.fileId, fileName: uploaded.fileName }];
    });
    return uploaded;
  }

  function handleRemoveAttachment(fileId: string) {
    setPendingAttachments((current) => current.filter((item) => item.fileId !== fileId));
  }

  if (!auth) {
    return <LoginPage onLogin={handleLogin} notice={loginNotice} />;
  }

  if (authBootstrapping) {
    return (
      <div className="login-root" role="status" aria-live="polite">
        <div className="login-card">
          <p style={{ margin: 0, textAlign: "center" }}>正在校验登录状态…</p>
        </div>
      </div>
    );
  }

  const isStreaming = messages.some(
    (item) => item.role === "assistant" && item.status === "running",
  );
  const panelRun = runs[selectedId] ?? makeRun({ sessionId });

  return (
    <AppShell
      sidebar={
        <Sidebar
          username={auth.username}
          activeView={view}
          sessions={sessions}
          activeSessionId={sessionId}
          checkpointStatus={checkpointStatus}
          onNewSession={handleNewSession}
          onSelectSession={handleSelectSession}
          onDeleteSession={handleDeleteSession}
          onOpenBaselines={() => setView("baseline")}
          onLogout={handleLogout}
        />
      }
      main={
        view === "baseline" ? (
          <ServiceBaselineManager />
        ) : (
          <ChatWorkspace
            mode={mode}
            messages={messages}
            runs={runs}
            inlineActivityEnabled={inlineActivityEnabled}
            granularActivityEnabled={granularActivityEnabled}
            runStatus={isStreaming ? "running" : "idle"}
            pendingAttachments={pendingAttachments}
            selectedId={selectedId}
            checkpointReplay={checkpointReplay}
            onCheckpointReplayChange={setCheckpointReplay}
            clarifyChips={panelRun.missingParams ?? panelRun.clarification?.missing_params ?? []}
            onModeChange={setMode}
            onSend={handleSend}
            onRemoveAttachment={handleRemoveAttachment}
            onUploadFile={handleUploadFile}
            onSelectMessage={setSelectedId}
            onRunFeedback={handleFeedback}
            onRunDistill={handleDistill}
            onRunConfirmSuggestion={handleConfirmSuggestion}
            onStop={handleStop}
          />
        )
      }
      panel={
        view === "baseline" ? (
          <div className="panel-card baseline-side-help">
            <h3>服务基线</h3>
            <p>
              录入每个服务关键指标（CPU/内存/QPS/P95）的正常区间。诊断时会作为“服务知识增强”附在
              指标/日志结果中，帮助区分噪声与真实异常。
            </p>
          </div>
        ) : inlineActivityEnabled ? undefined : (
          <AgentProcessPanel
            run={panelRun}
            onFeedback={(kind, actualRootCause) =>
              handleFeedback(selectedId, kind, actualRootCause)
            }
            onDistill={(action) => handleDistill(selectedId, action)}
            onConfirmSuggestion={(actionId) =>
              handleConfirmSuggestion(selectedId, actionId)
            }
          />
        )
      }
    />
  );
}
