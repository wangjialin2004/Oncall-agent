export type AgentMode = "auto" | "rag" | "oncall";
export type AgentRoute = "rag" | "aiops" | "clarify" | "unknown";
export type RunStatus = "idle" | "running" | "completed" | "error" | "cancelled";

export type RouteSelectedEvent = {
  type: "route_selected";
  route: AgentRoute;
  reason: string;
  mode: AgentMode;
};

export type TimelineEvent = {
  type: "agent_event" | "tool_event" | "decision_event";
  agent?: string;
  stage?: string;
  status?: string;
  summary?: string;
  tool?: string;
  evidence_id?: string;
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
};
