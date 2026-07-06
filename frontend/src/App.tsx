import { useCallback, useEffect, useRef, useState } from "react";

import { clearAuth, loadAuth, logout } from "./api/authApi";
import { streamAgent } from "./api/agentStream";
import { type CheckpointSummary, getCheckpoint } from "./api/checkpointApi";
import {
  type ConversationSummary,
  deleteConversation,
  getConversation,
  listConversations,
} from "./api/conversationApi";
import { uploadFile } from "./api/fileApi";
import { submitFeedback } from "./api/memoryApi";
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
    const key = [
      event.type,
      event.span_id,
      event.evidence_id,
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
  const saved = loadAuth();
  const [auth, setAuth] = useState<AuthState>(saved);

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
    } catch {
      // best-effort: the chat still works without the history list
      return;
    }
    // Fan out one GET /api/checkpoint per session. Failures degrade to "no
    // checkpoint" silently — the feature is opt-in, so missing data should
    // not break the sidebar.
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
  }, []);

  const loadSession = useCallback(async (sid: string) => {
    abortRef.current?.abort();
    activeIdRef.current = "";
    localStorage.setItem(SESSION_STORAGE_KEY, sid);
    setSessionId(sid);
    setView("chat");
    try {
      const turns = await getConversation(sid);
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
        restoredRuns[assistantId] = makeRun({
          runId: assistantId,
          sessionId: sid,
          route: (turn.route || "unknown") as AgentRoute,
          status: "completed",
          events: turn.events ?? [],
          answer: turn.assistant_answer,
          caseId: turn.case_id ?? "",
          userMessage: turn.user_message,
        });
        lastAssistantId = assistantId;
      }
      setMessages(restoredMessages);
      setRuns(restoredRuns);
      setSelectedId(lastAssistantId);
      // Refreshing the per-session checkpoint status here keeps the sidebar
      // badge and any "continue" affordances in sync right after switching.
      const status = await getCheckpoint(sid);
      setCheckpointStatus((current) =>
        status.enabled ? { ...current, [sid]: status } : current,
      );
    } catch {
      setMessages([]);
      setRuns({});
      setSelectedId("");
    }
  }, []);

  // On login (and initial mount with a saved session), hydrate history + the sidebar.
  useEffect(() => {
    if (!auth) {
      return;
    }
    void refreshSessions();
    void loadSession(sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth]);

  function handleLogin(token: string, username: string) {
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
                content: item.content || event.answer || "",
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
        next = { ...prev, events: normalizeTimelineEvents([...prev.events, event as TimelineEvent]) };
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
      if (!controller.signal.aborted) {
        setRuns((current) => ({
          ...current,
          [assistantId]: {
            ...(current[assistantId] ?? makeRun({ runId: assistantId, sessionId })),
            status: "error",
            error: error instanceof Error ? error.message : String(error),
          },
        }));
      }
    }
  }

  async function handleFeedback(kind: "adopted" | "corrected", actualRootCause = "") {
    const current = runs[selectedId];
    if (!current || !current.answer || !current.userMessage || current.feedback !== "") {
      return;
    }
    setRuns((c) => ({ ...c, [selectedId]: { ...c[selectedId], feedback: kind } }));
    try {
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
    try {
      await deleteConversation(sid);
    } catch {
      // best-effort
    }
    await refreshSessions();
    if (sid === sessionId) {
      handleNewSession();
    }
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
    return <LoginPage onLogin={handleLogin} />;
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
            runStatus={isStreaming ? "running" : "idle"}
            pendingAttachments={pendingAttachments}
            selectedId={selectedId}
            checkpointReplay={checkpointReplay}
            onCheckpointReplayChange={setCheckpointReplay}
            onModeChange={setMode}
            onSend={handleSend}
            onRemoveAttachment={handleRemoveAttachment}
            onUploadFile={handleUploadFile}
            onSelectMessage={setSelectedId}
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
        ) : (
          <AgentProcessPanel run={panelRun} onFeedback={handleFeedback} />
        )
      }
    />
  );
}
