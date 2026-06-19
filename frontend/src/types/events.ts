export type AgentMode = "auto" | "rag";
export type AgentRoute =
  | "knowledge"
  | "metric"
  | "log"
  | "change"
  | "diagnosis"
  | "clarify"
  | "unknown"
  | "error";
export type RunStatus = "idle" | "running" | "completed" | "error" | "cancelled";

export type RouteSelectedEvent = {
  type: "route_selected";
  route: AgentRoute;
  reason: string;
  mode: AgentMode;
};

export type TimelineEvent = {
  type: "route_event" | "agent_event" | "tool_event" | "decision_event";
  agent?: string;
  stage?: string;
  status?: string;
  summary?: string;
  tool?: string;
  route?: string;
  evidence_id?: string;
  duration_ms?: number;
  usage?: Record<string, number>;
  trace_id?: string;
  span_id?: string;
  payload?: Record<string, unknown>;
};

export type ContentEvent = {
  type: "content";
  data: string;
};

export type ReportEvent = {
  type: "report";
  route: AgentRoute;
  case_id: string;
  report: string;
};

export type CompleteEvent = {
  type: "complete";
  route: AgentRoute;
  answer: string;
  case_id: string;
  events: TimelineEvent[];
};

export type ErrorEvent = {
  type: "error";
  route?: AgentRoute;
  case_id?: string;
  message: string;
};

export type AgentStreamEvent =
  | RouteSelectedEvent
  | TimelineEvent
  | ContentEvent
  | ReportEvent
  | CompleteEvent
  | ErrorEvent;

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: RunStatus;
};

export type FeedbackState = "" | "adopted" | "corrected" | "weak";

export type AgentRun = {
  runId: string;
  sessionId: string;
  mode: AgentMode;
  route: AgentRoute;
  status: RunStatus;
  events: TimelineEvent[];
  answer: string;
  caseId: string;
  error: string;
  /** The user message that started this run, needed to build the experience card. */
  userMessage: string;
  /** Long-term-memory feedback already given for this run, if any. */
  feedback: FeedbackState;
};
