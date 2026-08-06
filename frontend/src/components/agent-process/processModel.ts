import type { AgentRun, TimelineEvent } from "../../types/events";
import {
  type ProcessDetailSection,
  presentAgent,
  presentEventDetails,
  presentEventSummary,
  presentRoute,
  presentTool,
} from "./processContent";

export type ProcessPhase = "route" | "plan" | "execute" | "verify" | "retry" | "report" | "exception";
export type ProcessStatus = "waiting" | "running" | "success" | "warning" | "error" | "skipped";

export type ProcessActivity = {
  id: string;
  title: string;
  status: ProcessStatus;
  summary: string;
  durationMs?: number;
  details: ProcessDetailSection[];
  /** Shared id for activities that should render side-by-side (parallel experts). */
  parallelGroupId?: string;
  /** Expert route key when this activity represents one delegated expert. */
  expertKey?: string;
};

export type ProcessStep = {
  id: string;
  order: number;
  phase: ProcessPhase;
  status: ProcessStatus;
  title: string;
  summary: string;
  durationMs?: number;
  activities: ProcessActivity[];
  details: ProcessDetailSection[];
  sourceEventIndexes: number[];
};

export type ProcessPanelModel = {
  routeLabel: string;
  status: ProcessStatus;
  currentStepId?: string;
  elapsedMs?: number;
  counts: { tools: number; experts: number; evidence: number };
  steps: ProcessStep[];
};

const STATUS_PRIORITY: Record<ProcessStatus, number> = {
  waiting: 0,
  skipped: 1,
  success: 2,
  running: 3,
  warning: 4,
  error: 5,
};

const EXCEPTION_STAGES = new Set([
  "budget",
  "no_progress",
  "step_timeout",
  "timeout_fallback",
  "timeout_soft_close",
  "fallback_start",
  "fallback_timeout",
  "knowledge_fallback_error",
  "knowledge_fallback_empty",
  "clarify",
  "clarify_missing_params",
  "error",
  "context_rehydrate_failed",
]);

const FOLDABLE_STAGES = new Set(["start", "started", "context", "context_rehydrate"]);

function eventStatus(event: TimelineEvent): ProcessStatus {
  const status = (event.status ?? "").toLowerCase();
  if (["failed", "failure", "error", "timeout", "timed_out"].includes(status)) {
    return "error";
  }
  if (["degraded", "partial", "evidence_insufficient"].includes(status)) {
    return "warning";
  }
  if (["running", "in_progress", "started", "pending"].includes(status)) {
    return "running";
  }
  if (["completed", "success", "ok", "root_cause_ready"].includes(status)) {
    return "success";
  }
  if (["cancelled", "canceled", "skipped"].includes(status)) {
    return "skipped";
  }
  return "waiting";
}

function mergeStatus(current: ProcessStatus, next: ProcessStatus): ProcessStatus {
  return STATUS_PRIORITY[next] > STATUS_PRIORITY[current] ? next : current;
}

function eventStepNumber(event: TimelineEvent): number | undefined {
  const raw = event.payload?.step;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string" && /^\d+$/.test(raw)) {
    return Number(raw);
  }
  return undefined;
}

function durationOf(event: TimelineEvent): number | undefined {
  return typeof event.duration_ms === "number" && Number.isFinite(event.duration_ms)
    ? event.duration_ms
    : undefined;
}

function activityId(event: TimelineEvent, index: number): string {
  return event.evidence_id || event.span_id || `${event.type}-${event.stage ?? event.tool ?? index}-${index}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

function parallelCallId(event: TimelineEvent, index: number): string {
  const payload = event.payload ?? {};
  const argumentsPayload = asRecord(payload.arguments) ?? {};
  return String(
    payload.tool_call_id ??
      payload.parent_tool_call_id ??
      argumentsPayload.tool_call_id ??
      event.evidence_id ??
      event.span_id ??
      `delegate-parallel-${index}`,
  );
}

function expertFromEvent(event: TimelineEvent): string {
  const payload = event.payload ?? {};
  const argumentsPayload = asRecord(payload.arguments) ?? {};
  const agent = String(event.agent ?? "");
  const agentExpert = agent.endsWith("_expert") ? agent.slice(0, -"_expert".length) : "";
  return String(
    payload.delegated_expert ??
      payload.expert ??
      argumentsPayload.expert ??
      agentExpert ??
      "",
  ).trim();
}

function expertActivityId(callId: string, expert: string): string {
  return `parallel:${callId}:${expert || "unknown"}`;
}

function serialDelegationId(event: TimelineEvent, index: number): string {
  const payload = event.payload ?? {};
  return String(
    payload.tool_call_id ??
      payload.parent_tool_call_id ??
      event.evidence_id ??
      event.span_id ??
      `delegate-${index}`,
  );
}

function expertsFromParallelEvent(event: TimelineEvent): string[] {
  const payload = event.payload ?? {};
  const argumentsPayload = asRecord(payload.arguments) ?? {};
  const fromPayload = stringList(payload.experts);
  if (fromPayload.length > 0) {
    return uniqueStrings(fromPayload);
  }
  const fromArgs = stringList(argumentsPayload.experts);
  if (fromArgs.length > 0) {
    return uniqueStrings(fromArgs);
  }
  const results = Array.isArray(payload.results)
    ? payload.results
    : Array.isArray(asRecord(payload.result)?.results)
      ? (asRecord(payload.result)?.results as unknown[])
      : [];
  const fromResults = results
    .map((item) => {
      const row = asRecord(item);
      return row ? String(row.expert ?? "").trim() : "";
    })
    .filter(Boolean);
  return uniqueStrings(fromResults);
}

function subtasksFromParallelEvent(event: TimelineEvent): string[] {
  const payload = event.payload ?? {};
  const argumentsPayload = asRecord(payload.arguments) ?? {};
  const fromPayload = stringList(payload.subtasks);
  if (fromPayload.length > 0) {
    return fromPayload;
  }
  return stringList(argumentsPayload.subtasks);
}

function uniqueStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const key = value.trim();
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(key);
  }
  return result;
}

function isParallelDelegateEvent(event: TimelineEvent): boolean {
  const stage = event.stage ?? "";
  return (
    stage === "delegate_parallel_start" ||
    stage === "delegate_parallel_done" ||
    event.tool === "delegate_parallel"
  );
}

function isSerialDelegateEvent(event: TimelineEvent): boolean {
  const stage = event.stage ?? "";
  return (
    stage === "delegate_start" ||
    stage === "delegate_dispatch" ||
    event.tool === "delegate_to_expert"
  );
}

function resultRowsFromParallelEvent(event: TimelineEvent): Record<string, unknown>[] {
  const payload = event.payload ?? {};
  const direct = Array.isArray(payload.results) ? payload.results : [];
  if (direct.length > 0) {
    return direct.map((item) => asRecord(item)).filter((item): item is Record<string, unknown> => Boolean(item));
  }
  const nested = asRecord(payload.result);
  const nestedResults = Array.isArray(nested?.results) ? nested.results : [];
  return nestedResults
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item));
}

function upsertActivity(step: ProcessStep, activity: ProcessActivity, index: number): void {
  const existingIndex = step.activities.findIndex((item) => item.id === activity.id);
  if (existingIndex >= 0) {
    const existing = step.activities[existingIndex];
    const mergedDetails = mergeActivityDetails(existing.details, activity.details);
    step.activities[existingIndex] = {
      ...existing,
      ...activity,
      status: mergeStatus(existing.status, activity.status),
      summary: preferRicherText(activity.summary, existing.summary),
      durationMs: activity.durationMs ?? existing.durationMs,
      details: mergedDetails,
      parallelGroupId: activity.parallelGroupId ?? existing.parallelGroupId,
      expertKey: activity.expertKey ?? existing.expertKey,
    };
  } else {
    step.activities.push(activity);
  }
  step.status = mergeStatus(step.status, activity.status);
  step.sourceEventIndexes.push(index);
}

function preferRicherText(next: string, prev: string): string {
  const a = (next || "").trim();
  const b = (prev || "").trim();
  if (!a) {
    return b;
  }
  if (!b) {
    return a;
  }
  // Prefer concrete conclusions over generic progress text.
  if (a.startsWith("任务：") && !b.startsWith("任务：") && b.length >= a.length) {
    return b;
  }
  return a.length >= b.length ? a : b;
}

function mergeActivityDetails(
  existing: ProcessDetailSection[],
  incoming: ProcessDetailSection[],
): ProcessDetailSection[] {
  if (incoming.length === 0) {
    return existing;
  }
  if (existing.length === 0) {
    return incoming;
  }
  const byId = new Map<string, ProcessDetailSection>();
  for (const section of existing) {
    byId.set(section.id, section);
  }
  for (const section of incoming) {
    const prev = byId.get(section.id);
    if (!prev) {
      byId.set(section.id, section);
      continue;
    }
    byId.set(section.id, {
      ...prev,
      ...section,
      summary: preferRicherText(section.summary ?? "", prev.summary ?? "") || undefined,
      fields: section.fields.length > 0 ? section.fields : prev.fields,
      items: section.items.length > 0 ? section.items : prev.items,
      raw: section.raw ?? prev.raw,
      remaining: section.remaining ?? prev.remaining,
    });
  }
  // Keep stable order: previous sections first, then newly introduced ones.
  const ordered: ProcessDetailSection[] = [];
  const seen = new Set<string>();
  for (const section of [...existing, ...incoming]) {
    if (seen.has(section.id)) {
      continue;
    }
    const merged = byId.get(section.id);
    if (merged) {
      ordered.push(merged);
      seen.add(section.id);
    }
  }
  return ordered;
}

function applyParallelDelegateEvent(
  step: ProcessStep,
  event: TimelineEvent,
  index: number,
  expertIds: Set<string>,
): void {
  const callId = parallelCallId(event, index);
  const experts = expertsFromParallelEvent(event);
  const subtasks = subtasksFromParallelEvent(event);
  const resultRows = resultRowsFromParallelEvent(event);
  const resultByExpert = new Map(
    resultRows.map((row) => [String(row.expert ?? "").trim(), row] as const).filter(([key]) => Boolean(key)),
  );
  const wallMsRaw = event.payload?.wall_ms;
  const wallMs =
    typeof wallMsRaw === "number" && Number.isFinite(wallMsRaw)
      ? wallMsRaw
      : typeof asRecord(event.payload?.result)?.wall_ms === "number"
        ? Number(asRecord(event.payload?.result)?.wall_ms)
        : undefined;

  if (experts.length === 0) {
    // Fallback: keep a single activity when payload has no expert list.
    upsertActivity(
      step,
      {
        id: `parallel:${callId}`,
        title: presentTool("delegate_parallel"),
        status: eventStatus(event),
        summary: presentEventSummary(event) || "并行委派专家",
        durationMs: durationOf(event) ?? wallMs,
        details: presentEventDetails(event),
        parallelGroupId: callId,
      },
      index,
    );
    expertIds.add(`parallel:${callId}`);
  } else {
    experts.forEach((expert, expertIndex) => {
      const result = resultByExpert.get(expert);
      const resultStatus = result ? eventStatus({ ...event, status: String(result.status ?? event.status ?? "") }) : eventStatus(event);
      const subtask = subtasks[expertIndex] ?? String(result?.subtask ?? "");
      const answer = String(result?.answer ?? result?.error ?? "").trim();
      const details: ProcessDetailSection[] = [];
      if (subtask) {
        details.push({
          id: `subtask-${expert}`,
          kind: "input",
          title: "执行内容",
          fields: [{ label: "子任务", value: subtask.length > 240 ? `${subtask.slice(0, 240)}…` : subtask }],
          items: [],
        });
      }
      if (answer) {
        details.push({
          id: `result-${expert}`,
          kind: "result",
          title: "关键结果",
          fields: [{ label: "结论", value: answer.length > 240 ? `${answer.slice(0, 240)}…` : answer }],
          items: [],
        });
      }
      if (details.length === 0) {
        details.push(...presentEventDetails(event));
      }
      upsertActivity(
        step,
        {
          id: expertActivityId(callId, expert),
          title: presentAgent(expert),
          status:
            event.stage === "delegate_parallel_start" ||
            (event.tool === "delegate_parallel" && event.status === "in_progress")
              ? "running"
              : resultStatus,
          summary:
            answer ||
            (subtask ? `任务：${subtask.length > 100 ? `${subtask.slice(0, 100)}…` : subtask}` : "") ||
            presentEventSummary(event) ||
            `${presentAgent(expert)}并行取证`,
          durationMs: durationOf(event) ?? wallMs,
          details,
          parallelGroupId: callId,
          expertKey: expert,
        },
        index,
      );
      expertIds.add(expertActivityId(callId, expert));
    });
  }

  if (event.type === "tool_event" || event.stage === "delegate_parallel_start") {
    step.summary =
      experts.length > 0
        ? `并行委派 ${experts.map((item) => presentAgent(item)).join("、")}`
        : presentEventSummary(event) || step.summary || "并行委派专家";
    if (step.phase === "execute" || step.phase === "retry") {
      step.title = experts.length > 1 ? "并行专家取证" : step.title;
    }
  }
}

function applySerialDelegateEvent(
  step: ProcessStep,
  event: TimelineEvent,
  index: number,
  expertIds: Set<string>,
): void {
  const expert = expertFromEvent(event);
  const callId = serialDelegationId(event, index);
  const activityIdValue = expert ? `serial:${callId}:${expert}` : `serial:${callId}`;
  const argumentsPayload = asRecord(event.payload?.arguments) ?? {};
  const subtask = String(event.payload?.subtask ?? argumentsPayload.subtask ?? "").trim();
  const result = asRecord(event.payload?.result);
  const answer = String(result?.answer ?? event.payload?.answer ?? "").trim();
  const details: ProcessDetailSection[] = [];
  if (subtask) {
    const fields = [
      ...(expert ? [{ label: "专家", value: presentAgent(expert) }] : []),
      { label: "子任务", value: subtask.length > 240 ? `${subtask.slice(0, 240)}…` : subtask },
    ];
    details.push({
      id: "subtask",
      kind: "input",
      title: "执行内容",
      // Keep summary empty when fields already carry the subtask text to avoid
      // duplicate visible strings in the process panel.
      fields,
      items: [],
    });
  }
  if (answer) {
    const fields = [
      ...(expert && !subtask ? [{ label: "专家", value: presentAgent(expert) }] : []),
      { label: "结论", value: answer.length > 240 ? `${answer.slice(0, 240)}…` : answer },
    ];
    details.push({
      id: "result",
      kind: "result",
      title: "关键结果",
      fields,
      items: [],
    });
  }
  if (details.length === 0) {
    details.push(...presentEventDetails(event));
  }
  upsertActivity(
    step,
    {
      id: activityIdValue,
      title: expert ? `委派${presentAgent(expert)}` : presentTool("delegate_to_expert"),
      status: eventStatus(event),
      summary:
        answer ||
        (subtask ? `任务：${subtask.length > 100 ? `${subtask.slice(0, 100)}…` : subtask}` : "") ||
        presentEventSummary(event),
      durationMs: durationOf(event),
      details,
      expertKey: expert || undefined,
    },
    index,
  );
  expertIds.add(activityIdValue);
  if (event.type === "tool_event" || !step.summary) {
    step.summary = expert ? `委派${presentAgent(expert)}` : presentEventSummary(event) || step.summary;
  }
}

function applyChildExpertEvent(
  step: ProcessStep,
  event: TimelineEvent,
  index: number,
  expertIds: Set<string>,
): boolean {
  const payload = event.payload ?? {};
  const expert = expertFromEvent(event);
  const parentCallId = String(payload.parent_tool_call_id ?? "").trim();
  const isParallelChild = Boolean(payload.parallel) || Boolean(parentCallId);
  if (!expert || !isParallelChild) {
    return false;
  }
  const callId = parentCallId || parallelCallId(event, index);
  const id = expertActivityId(callId, expert);
  const existing = step.activities.find((item) => item.id === id);
  const childDetails = presentEventDetails(event);
  const childSummary = presentEventSummary(event);
  upsertActivity(
    step,
    {
      id,
      title: presentAgent(expert),
      status: eventStatus(event),
      summary: childSummary || existing?.summary || `${presentAgent(expert)}取证中`,
      durationMs: durationOf(event) ?? existing?.durationMs,
      details: childDetails,
      parallelGroupId: callId,
      expertKey: expert,
    },
    index,
  );
  expertIds.add(id);
  return true;
}

function activityFromEvent(event: TimelineEvent, index: number): ProcessActivity {
  const delegatedExpert = expertFromEvent(event);
  return {
    id: activityId(event, index),
    title:
      event.stage?.startsWith("delegate_") && delegatedExpert
        ? `委派${presentAgent(delegatedExpert)}`
        : event.type === "tool_event"
          ? presentTool(event.tool)
          : presentAgent(event.agent),
    status: eventStatus(event),
    summary: presentEventSummary(event),
    durationMs: durationOf(event),
    details: presentEventDetails(event),
    expertKey: delegatedExpert || undefined,
  };
}

function addActivity(
  step: ProcessStep,
  event: TimelineEvent,
  index: number,
  expertIds: Set<string>,
): void {
  if (isParallelDelegateEvent(event)) {
    applyParallelDelegateEvent(step, event, index, expertIds);
    return;
  }
  if (isSerialDelegateEvent(event)) {
    applySerialDelegateEvent(step, event, index, expertIds);
    return;
  }
  if (applyChildExpertEvent(step, event, index, expertIds)) {
    return;
  }

  const activity = activityFromEvent(event, index);
  upsertActivity(step, activity, index);
  if (activity.summary && (event.type === "tool_event" || !step.summary)) {
    step.summary = activity.summary;
  }
  if (event.type === "tool_event" && step.activities.filter((item) => item.title === activity.title).length === 1) {
    step.title = presentTool(event.tool);
  }
}

function makeStep(
  steps: ProcessStep[],
  input: Omit<ProcessStep, "order" | "activities" | "details" | "sourceEventIndexes"> &
    Partial<Pick<ProcessStep, "activities" | "details" | "sourceEventIndexes">>,
): ProcessStep {
  const step: ProcessStep = {
    ...input,
    order: steps.length + 1,
    activities: input.activities ?? [],
    details: input.details ?? [],
    sourceEventIndexes: input.sourceEventIndexes ?? [],
  };
  steps.push(step);
  return step;
}

function finalize(step: ProcessStep | undefined): void {
  if (step?.status === "running") {
    step.status = "success";
  }
}

function planDetails(event: TimelineEvent): ProcessDetailSection[] {
  const todos = Array.isArray(event.payload?.todos)
    ? event.payload.todos.map((item) => String(item)).filter(Boolean)
    : [];
  const details = presentEventDetails(event);
  if (todos.length > 0) {
    details.unshift({
      id: "plan-items",
      kind: "input",
      title: "执行内容",
      summary: `已规划 ${todos.length} 项`,
      fields: [],
      items: todos.slice(0, 8),
      remaining: Math.max(0, todos.length - 8),
    });
  }
  const requiredParams = Array.isArray(event.payload?.required_params)
    ? event.payload.required_params
        .map((item, index) => {
          if (!item || typeof item !== "object") {
            return String(item || `参数 ${index + 1}`);
          }
          const row = item as Record<string, unknown>;
          const prompt = String(row.prompt ?? row.name ?? `参数 ${index + 1}`);
          const reason = String(row.reason ?? "");
          return reason ? `${prompt}：${reason}` : prompt;
        })
        .filter(Boolean)
    : [];
  if (requiredParams.length > 0) {
    details.splice(todos.length > 0 ? 1 : 0, 0, {
      id: "plan-params",
      kind: "input",
      title: "执行内容",
      summary: `待补充 ${requiredParams.length} 项参数`,
      fields: [],
      items: requiredParams,
    });
  }
  return details;
}

function reportDetails(run: AgentRun, event: TimelineEvent): ProcessDetailSection[] {
  const details = presentEventDetails(event);
  if (run.answer) {
    details.unshift({
      id: "final-report",
      kind: "result",
      title: "关键结果",
      summary: "最终报告已生成，完整内容以对话区为准。",
      fields: [],
      items: [],
      raw: run.answer,
    });
  }
  return details;
}

export function buildProcessPanelModel(run: AgentRun): ProcessPanelModel {
  const steps: ProcessStep[] = [];
  const executionByNumber = new Map<number, ProcessStep>();
  const toolIds = new Set<string>();
  const evidenceIds = new Set<string>();
  const expertIds = new Set<string>();
  let currentExecution: ProcessStep | undefined;
  let currentVerification: ProcessStep | undefined;
  let currentRetry: ProcessStep | undefined;
  let planVersion = 0;
  let executionSequence = 0;
  let verifySequence = 0;
  let retrySequence = 0;
  let reportStep: ProcessStep | undefined;
  let elapsedMs: number | undefined;

  for (const [index, event] of run.events.entries()) {
    const stage = event.stage ?? "";
    const status = eventStatus(event);

    if (event.type === "tool_event") {
      const id = activityId(event, index);
      // Parallel/serial delegate tools are counted via expert activity ids, not raw tool ids.
      if (!["delegate_to_expert", "delegate_parallel"].includes(event.tool ?? "")) {
        toolIds.add(id);
      }
      if (status === "success" && !["delegate_to_expert", "delegate_parallel"].includes(event.tool ?? "")) {
        evidenceIds.add(event.evidence_id || id);
      }
    }

    if (event.type === "route_event") {
      const existing = steps.find((step) => step.phase === "route");
      if (existing) {
        existing.status = status;
        existing.summary = presentEventSummary(event);
        existing.details = presentEventDetails(event);
        existing.sourceEventIndexes.push(index);
      } else {
        makeStep(steps, {
          id: "route",
          phase: "route",
          status,
          title: "识别请求",
          summary: presentEventSummary(event),
          durationMs: durationOf(event),
          details: presentEventDetails(event),
          sourceEventIndexes: [index],
        });
      }
      continue;
    }

    if (stage === "plan" || stage === "planning" || stage === "replan") {
      finalize(currentExecution);
      planVersion += 1;
      makeStep(steps, {
        id: planVersion === 1 ? "plan" : `plan-${planVersion}`,
        phase: "plan",
        status,
        title: planVersion === 1 ? "制定排查计划" : `调整排查计划（第 ${planVersion - 1} 次）`,
        summary:
          presentEventSummary(event) ||
          `已规划 ${Array.isArray(event.payload?.todos) ? event.payload.todos.length : 0} 项排查内容`,
        durationMs: durationOf(event),
        details: planDetails(event),
        sourceEventIndexes: [index],
      });
      continue;
    }

    if (stage === "model_decision" || stage === "decision") {
      finalize(currentExecution);
      currentVerification = undefined;
      currentRetry = undefined;
      const stepNumber = eventStepNumber(event) ?? ++executionSequence;
      executionSequence = Math.max(executionSequence, stepNumber);
      currentExecution = executionByNumber.get(stepNumber);
      if (!currentExecution) {
        currentExecution = makeStep(steps, {
          id: `execute-${stepNumber}`,
          phase: "execute",
          status,
          title: `分析与取证（第 ${stepNumber} 轮）`,
          summary: presentEventSummary(event) || "正在判断下一步动作",
          durationMs: durationOf(event),
          sourceEventIndexes: [index],
        });
        executionByNumber.set(stepNumber, currentExecution);
      } else {
        addActivity(currentExecution, event, index, expertIds);
      }
      continue;
    }

    if (stage === "re_evidence") {
      finalize(currentExecution);
      currentVerification = undefined;
      retrySequence += 1;
      currentRetry = makeStep(steps, {
        id: `retry-${retrySequence}`,
        phase: "retry",
        status: status === "running" ? "warning" : status,
        title: `补充取证（第 ${retrySequence} 轮）`,
        summary: presentEventSummary(event) || "证据仍有缺口，正在补充取证",
        durationMs: durationOf(event),
        details: presentEventDetails(event),
        sourceEventIndexes: [index],
      });
      currentExecution = currentRetry;
      continue;
    }

    if (
      event.type === "tool_event" ||
      stage.startsWith("delegate_") ||
      stage.startsWith("log_") ||
      stage === "aux_probe" ||
      (event.agent?.endsWith("_expert") && (stage === "start" || stage === "complete" || stage === "evidence_return"))
    ) {
      const stepNumber = eventStepNumber(event);
      const target =
        (stepNumber !== undefined ? executionByNumber.get(stepNumber) : undefined) ??
        currentRetry ??
        currentExecution ??
        makeStep(steps, {
          id: `execute-${++executionSequence}`,
          phase: "execute",
          status: "running",
          title: "分析与取证",
          summary: "正在执行只读取证",
        });
      currentExecution = target;
      if (!executionByNumber.has(executionSequence) && target.phase === "execute") {
        executionByNumber.set(executionSequence, target);
      }
      addActivity(target, event, index, expertIds);
      continue;
    }

    if (stage === "verify" || stage === "verification") {
      finalize(currentExecution);
      if (!currentVerification) {
        verifySequence += 1;
        currentVerification = makeStep(steps, {
          id: `verify-${verifySequence}`,
          phase: "verify",
          status,
          title: verifySequence === 1 ? "证据自检" : `证据自检（第 ${verifySequence} 次）`,
          summary: presentEventSummary(event) || "正在核对结论与证据",
          durationMs: durationOf(event),
          details: presentEventDetails(event),
          sourceEventIndexes: [index],
        });
      } else {
        currentVerification.status = mergeStatus(currentVerification.status, status);
        if (status !== "running") {
          currentVerification.status = status;
        }
        currentVerification.summary = presentEventSummary(event) || currentVerification.summary;
        currentVerification.durationMs = durationOf(event) ?? currentVerification.durationMs;
        currentVerification.details = presentEventDetails(event);
        currentVerification.sourceEventIndexes.push(index);
      }
      continue;
    }

    if (stage === "report") {
      finalize(currentExecution);
      finalize(currentVerification);
      reportStep = reportStep ?? makeStep(steps, {
        id: "report",
        phase: "report",
        status,
        title: "生成结论",
        summary: presentEventSummary(event) || "正在生成最终报告",
        durationMs: durationOf(event),
        details: reportDetails(run, event),
        sourceEventIndexes: [index],
      });
      currentVerification = undefined;
      continue;
    }

    if (stage === "complete" && event.agent === "harness") {
      finalize(currentExecution);
      finalize(currentVerification);
      elapsedMs = durationOf(event) ?? elapsedMs;
      if (!reportStep) {
        reportStep = makeStep(steps, {
          id: "report",
          phase: "report",
          status,
          title: "生成结论",
          summary: presentEventSummary(event) || "本轮诊断已完成",
          durationMs: durationOf(event),
          details: reportDetails(run, event),
          sourceEventIndexes: [index],
        });
      } else {
        reportStep.status = status;
        reportStep.summary = presentEventSummary(event) || reportStep.summary;
        reportStep.durationMs = durationOf(event) ?? reportStep.durationMs;
        reportStep.details = reportDetails(run, event);
        reportStep.sourceEventIndexes.push(index);
      }
      continue;
    }

    if (EXCEPTION_STAGES.has(stage)) {
      finalize(currentExecution);
      const exceptionStatus = status === "error" ? "error" : "warning";
      makeStep(steps, {
        id: `exception-${stage || index}-${index}`,
        phase: "exception",
        status: exceptionStatus,
        title:
          stage.includes("clarify")
            ? "等待补充信息"
            : stage.includes("timeout")
              ? "步骤超时"
              : stage.includes("fallback")
                ? "进入降级路径"
                : "执行受阻",
        summary: presentEventSummary(event) || "执行过程中出现需要关注的情况",
        durationMs: durationOf(event),
        details: presentEventDetails(event),
        sourceEventIndexes: [index],
      });
      continue;
    }

    if (FOLDABLE_STAGES.has(stage)) {
      if (status === "error" || status === "warning") {
        makeStep(steps, {
          id: `exception-${stage}-${index}`,
          phase: "exception",
          status,
          title: "上下文准备异常",
          summary: presentEventSummary(event),
          durationMs: durationOf(event),
          details: presentEventDetails(event),
          sourceEventIndexes: [index],
        });
      }
      continue;
    }

    if (stage === "complete" && currentExecution) {
      addActivity(currentExecution, event, index, expertIds);
      continue;
    }
  }

  if (run.status === "completed") {
    finalize(currentExecution);
    finalize(currentVerification);
  }

  const currentStep =
    run.status === "running" || run.status === "idle"
      ? [...steps].reverse().find((step) => step.status === "running" || step.status === "warning")
      : undefined;

  return {
    routeLabel: presentRoute(run.route),
    status:
      run.status === "error"
        ? "error"
        : run.status === "cancelled"
          ? "skipped"
          : run.status === "completed"
            ? "success"
            : run.status === "running"
              ? "running"
              : "waiting",
    currentStepId: currentStep?.id,
    elapsedMs,
    counts: {
      tools: toolIds.size,
      experts: expertIds.size,
      evidence: evidenceIds.size,
    },
    steps: steps.map((step, index) => ({ ...step, order: index + 1 })),
  };
}
