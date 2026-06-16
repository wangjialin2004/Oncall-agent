import { useMemo, useRef, useState } from "react";

import { streamAgent } from "./api/agentStream";
import { AgentProcessPanel } from "./components/AgentProcessPanel";
import { AppShell } from "./components/AppShell";
import { ChatWorkspace } from "./components/ChatWorkspace";
import { Sidebar } from "./components/Sidebar";
import type {
  AgentMode,
  AgentRun,
  AgentStreamEvent,
  ChatMessage,
  TimelineEvent,
} from "./types/events";

const initialRun: AgentRun = {
  runId: "",
  sessionId: "session-default",
  mode: "auto",
  route: "unknown",
  status: "idle",
  events: [],
  answer: "",
  caseId: "",
  error: "",
};

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function App() {
  const [mode, setMode] = useState<AgentMode>("auto");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [run, setRun] = useState<AgentRun>(initialRun);
  const abortRef = useRef<AbortController | null>(null);
  const sessionId = useMemo(() => createId("session"), []);

  function applyEvent(event: AgentStreamEvent) {
    if (event.type === "complete") {
      setMessages((items) => [
        ...items.filter((item) => item.status !== "running"),
        { id: createId("assistant"), role: "assistant", content: event.answer, status: "completed" },
      ]);
    }

    setRun((current) => {
      if (event.type === "route_selected") {
        return { ...current, route: event.route, mode: event.mode, status: "running" };
      }
      if (
        event.type === "agent_event" ||
        event.type === "tool_event" ||
        event.type === "decision_event"
      ) {
        return { ...current, events: [...current.events, event as TimelineEvent] };
      }
      if (event.type === "content") {
        return { ...current, answer: `${current.answer}${event.data}` };
      }
      if (event.type === "report") {
        return { ...current, caseId: event.case_id, answer: event.report };
      }
      if (event.type === "complete") {
        return {
          ...current,
          route: event.route,
          status: "completed",
          answer: event.answer,
          caseId: event.case_id,
          events: event.events.length > 0 ? event.events : current.events,
        };
      }
      if (event.type === "error") {
        return {
          ...current,
          status: "error",
          error: event.message,
          caseId: event.case_id ?? current.caseId,
        };
      }
      return {
        ...current,
      };
    });
  }

  async function handleSend(message: string) {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const runId = createId("run");

    setMessages((items) => [
      ...items,
      { id: createId("user"), role: "user", content: message },
      { id: createId("assistant"), role: "assistant", content: "Running...", status: "running" },
    ]);
    setRun({
      ...initialRun,
      runId,
      sessionId,
      mode,
      status: "running",
    });

    try {
      await streamAgent({
        sessionId,
        message,
        mode,
        signal: controller.signal,
        onEvent: applyEvent,
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        setRun((current) => ({
          ...current,
          status: "error",
          error: error instanceof Error ? error.message : String(error),
        }));
      }
    }
  }

  function handleNewSession() {
    abortRef.current?.abort();
    setMessages([]);
    setRun({ ...initialRun, sessionId });
  }

  function handleStop() {
    abortRef.current?.abort();
    setRun((current) => ({ ...current, status: "cancelled" }));
  }

  return (
    <AppShell
      sidebar={<Sidebar onNewSession={handleNewSession} />}
      main={
        <ChatWorkspace
          mode={mode}
          messages={messages}
          runStatus={run.status}
          onModeChange={setMode}
          onSend={handleSend}
          onStop={handleStop}
        />
      }
      panel={<AgentProcessPanel run={run} />}
    />
  );
}
