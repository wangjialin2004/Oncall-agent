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
  timelineEvent?: TimelineEvent;
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
  started_at?: number;
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

export type DistillDraftInfo = {
  experience_id: string;
  status: string;
  enabled?: boolean;
  requires_confirm?: boolean;
};

export type ClarificationInfo = {
  missing_params: string[];
  defaults?: Record<string, string>;
  question?: string;
};

/** Read-only HITL suggestion emitted on complete (M3 W9). Confirm = audit only. */
export type SuggestedAction = {
  id: string;
  title: string;
  risk: "low" | "medium" | "high" | string;
  requires_confirm: boolean;
};

export type CompleteEvent = {
  type: "complete";
  route: AgentRoute;
  answer: string;
  /** True when the server verified/replaced already streamed draft prose. */
  replace_streamed_answer?: boolean;
  case_id: string;
  events: TimelineEvent[];
  distill_draft?: DistillDraftInfo | null;
  missing_params?: string[];
  clarification?: ClarificationInfo | null;
  suggested_actions?: SuggestedAction[];
};

export type ErrorEvent = {
  type: "error";
  route?: AgentRoute;
  case_id?: string;
  message: string;
};

/**
 * Surfaced at the start of a resumed run when the harness loop picks up an
 * existing checkpoint for this session. Distinct from a timeline event so it
 * cannot be deduped or replayed through the normal timeline channel.
 */
export type CheckpointResumeEvent = {
  type: "checkpoint_resume";
  resumedFromStep: number;
  replayedSteps: number;
  startedAt: string;
  /** Mirrors backend ``config.harness_checkpoint_replay`` at the time of resume. */
  conservative: boolean;
  /** ``true`` / ``false`` if the caller passed ``CheckpointReplay`` in the body,
   *  ``null`` when the request fell back to the server default. */
  replayOverride: boolean | null;
};

/**
 * Surfaced mid-run when the harness decided to close the resumed run without
 * replaying any non-whitelisted tool. Only ever emitted alongside a
 * ``CheckpointResumeEvent``.
 */
export type CheckpointConservativeCloseEvent = {
  type: "checkpoint_conservative_close";
  step: number;
};

export type AgentStreamEvent =
  | RouteSelectedEvent
  | TimelineEvent
  | ContentEvent
  | ReportEvent
  | CompleteEvent
  | ErrorEvent
  | CheckpointResumeEvent
  | CheckpointConservativeCloseEvent;

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
  /** Auto-distill draft from harness complete (W10), if any. */
  distillDraft?: DistillDraftInfo | null;
  /** Whether the pending distill draft was confirmed/rejected from the panel. */
  distillStatus?: "" | "confirmed" | "rejected";
  /** Structured clarification slots for quick-fill chips (W10). */
  missingParams?: string[];
  clarification?: ClarificationInfo | null;
  /** Read-only follow-up suggestions from harness complete (W9). */
  suggestedActions?: SuggestedAction[];
  /** Action ids the operator has confirmed this session (audit-only). */
  confirmedActionIds?: string[];
  /** Set when this run picked up a saved checkpoint from Redis. */
  checkpointResume?: CheckpointResumeEvent;
  /** Set when the resumed run closed without re-running non-whitelisted tools. */
  checkpointConservativeClose?: CheckpointConservativeCloseEvent;
};
