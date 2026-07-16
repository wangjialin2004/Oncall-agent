import { describe, expect, it } from "vitest";

import type { AgentRun, TimelineEvent } from "../../../types/events";
import { buildProcessPanelModel } from "../processModel";

function event(type: TimelineEvent["type"], input: Omit<TimelineEvent, "type">): TimelineEvent {
  return { type, ...input };
}

function makeRun(events: TimelineEvent[], status: AgentRun["status"] = "running"): AgentRun {
  return {
    runId: "run-1",
    sessionId: "session-1",
    mode: "auto",
    route: "diagnosis",
    status,
    events,
    answer: status === "completed" ? "最终诊断报告" : "",
    caseId: "",
    error: "",
    userMessage: "排查故障",
    feedback: "",
  };
}

describe("buildProcessPanelModel", () => {
  it("merges lifecycle events into business steps", () => {
    const model = buildProcessPanelModel(
      makeRun(
        [
          event("route_event", { route: "diagnosis", status: "completed" }),
          event("agent_event", { agent: "harness", stage: "start", status: "in_progress" }),
          event("agent_event", {
            agent: "harness",
            stage: "plan",
            status: "completed",
            payload: { todos: ["查指标"] },
          }),
          event("agent_event", {
            agent: "harness",
            stage: "model_decision",
            status: "in_progress",
            payload: { step: 1 },
          }),
          event("tool_event", {
            agent: "harness",
            tool: "query_metrics",
            status: "completed",
            evidence_id: "call-1",
          }),
          event("agent_event", { agent: "harness", stage: "verify", status: "in_progress" }),
          event("agent_event", {
            agent: "harness",
            stage: "verify",
            status: "completed",
            payload: { evidence_count: 1 },
          }),
          event("agent_event", { agent: "harness", stage: "report", status: "in_progress" }),
          event("agent_event", { agent: "harness", stage: "complete", status: "completed" }),
        ],
        "completed",
      ),
    );

    expect(model.steps.map((step) => step.phase)).toEqual(["route", "plan", "execute", "verify", "report"]);
    expect(model.steps).toHaveLength(5);
    expect(model.counts).toEqual({ tools: 1, experts: 0, evidence: 1 });
  });

  it("preserves a failed execution step after the run closes", () => {
    const model = buildProcessPanelModel(
      makeRun(
        [
          event("agent_event", {
            agent: "harness",
            stage: "model_decision",
            status: "in_progress",
            payload: { step: 1 },
          }),
          event("tool_event", {
            agent: "harness",
            tool: "query_logs",
            status: "failed",
            evidence_id: "call-1",
          }),
          event("agent_event", { agent: "harness", stage: "complete", status: "completed" }),
        ],
        "completed",
      ),
    );

    expect(model.steps.find((step) => step.phase === "execute")?.status).toBe("error");
  });

  it("merges verification progress and terminal events", () => {
    const model = buildProcessPanelModel(
      makeRun([
        event("agent_event", { agent: "harness", stage: "verify", status: "in_progress" }),
        event("agent_event", {
          agent: "harness",
          stage: "verify",
          status: "degraded",
          payload: { gaps: ["缺少日志样本"] },
        }),
      ]),
    );

    const verifySteps = model.steps.filter((step) => step.phase === "verify");
    expect(verifySteps).toHaveLength(1);
    expect(verifySteps[0].status).toBe("warning");
    expect(verifySteps[0].sourceEventIndexes).toEqual([0, 1]);
  });

  it("does not duplicate a verification step when a terminal event is replayed", () => {
    const verifyComplete = event("agent_event", {
      agent: "harness",
      stage: "verify",
      status: "completed",
      payload: { evidence_count: 1 },
    });
    const model = buildProcessPanelModel(
      makeRun([
        event("agent_event", { agent: "harness", stage: "verify", status: "in_progress" }),
        verifyComplete,
        { ...verifyComplete, summary: "verification complete" },
      ]),
    );

    expect(model.steps.filter((step) => step.phase === "verify")).toHaveLength(1);
  });

  it("deduplicates delegate start and tool completion by tool call id", () => {
    const model = buildProcessPanelModel(
      makeRun([
        event("agent_event", {
          agent: "harness",
          stage: "delegate_start",
          status: "in_progress",
          payload: { delegated_expert: "metric", tool_call_id: "call-metric" },
        }),
        event("tool_event", {
          agent: "harness",
          tool: "delegate_to_expert",
          status: "completed",
          evidence_id: "call-metric",
          payload: { tool_call_id: "call-metric" },
        }),
      ]),
    );

    expect(model.counts.experts).toBe(1);
  });

  it("keeps planned todos as content instead of inferred completion", () => {
    const model = buildProcessPanelModel(
      makeRun(
        [
          event("agent_event", {
            agent: "harness",
            stage: "plan",
            status: "completed",
            payload: { todos: ["查询日志", "核对变更", "证据自检"] },
          }),
          event("agent_event", { agent: "harness", stage: "complete", status: "completed" }),
        ],
        "completed",
      ),
    );

    const plan = model.steps.find((step) => step.phase === "plan");
    expect(plan?.details.find((detail) => detail.id === "plan-items")?.items).toEqual([
      "查询日志",
      "核对变更",
      "证据自检",
    ]);
    expect(JSON.stringify(plan)).not.toContain("status-done");
  });
});
