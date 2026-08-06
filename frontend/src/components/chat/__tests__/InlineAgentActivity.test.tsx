import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { AgentRun } from "../../../types/events";
import { InlineAgentActivity } from "../InlineAgentActivity";

afterEach(cleanup);

function makeRun(status: AgentRun["status"] = "running"): AgentRun {
  return {
    runId: "assistant-1",
    sessionId: "session-1",
    mode: "auto",
    route: "diagnosis",
    status,
    events: [
      { type: "route_event", route: "diagnosis", status: "completed" },
      {
        type: "agent_event",
        stage: "delegate_parallel_start",
        status: "in_progress",
        payload: { experts: ["metric", "log", "change"], tool_call_id: "activity-1" },
      },
    ],
    answer: status === "completed" ? "final answer" : "",
    caseId: "",
    error: "",
    userMessage: "check cpu",
    feedback: "",
  };
}

describe("InlineAgentActivity", () => {
  it("shows meaningful progress while the assistant answer is still empty", () => {
    render(<InlineAgentActivity run={makeRun()} />);

    expect(screen.getByText("正在排查 · 2 步 · 3 位专家")).toBeInTheDocument();
    expect(screen.getByText("已识别为综合诊断")).toBeInTheDocument();
    expect(screen.getByText("指标专家")).toBeInTheDocument();
    expect(screen.getByText("日志专家")).toBeInTheDocument();
    expect(screen.getByText("变更专家")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "助手执行过程" })).toBeVisible();
  });

  it("collapses completed details and reopens them with keyboard", async () => {
    const user = userEvent.setup();
    render(<InlineAgentActivity run={makeRun("completed")} />);

    const toggle = screen.getByRole("button", { name: /已完成/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("已识别为综合诊断")).not.toBeInTheDocument();

    toggle.focus();
    await user.keyboard(" ");

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("已识别为综合诊断")).toBeInTheDocument();
  });

  it("keeps failed details visible instead of reporting completion", () => {
    const { rerender } = render(<InlineAgentActivity run={makeRun()} />);

    rerender(<InlineAgentActivity run={makeRun("error")} />);

    const toggle = screen.getByRole("button", { name: /执行失败/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("已识别为综合诊断")).toBeInTheDocument();
  });

  it("renders tool status and duration without exposing raw event data", () => {
    const run = makeRun();
    run.events = [
      {
        type: "agent_event",
        stage: "tool_start",
        status: "in_progress",
        payload: {
          tool: "query_prometheus_alerts",
          tool_call_id: "activity-2",
          arguments: { keyword: "SECRET" },
        },
      },
      {
        type: "tool_event",
        tool: "query_prometheus_alerts",
        status: "completed",
        duration_ms: 1250,
        payload: { tool_call_id: "activity-2", result: "SECRET_RESULT" },
      },
    ];

    render(<InlineAgentActivity run={run} />);

    expect(screen.getByText("查询告警")).toBeInTheDocument();
    expect(screen.getByText("1.3 秒")).toBeInTheDocument();
    expect(screen.queryByText("SECRET")).not.toBeInTheDocument();
    expect(screen.queryByText("SECRET_RESULT")).not.toBeInTheDocument();
  });

  it("keeps a child Agent MCP tool inside that Agent branch", () => {
    const run = makeRun();
    run.events = [
      {
        type: "agent_event",
        stage: "delegate_parallel_start",
        status: "in_progress",
        payload: { experts: ["metric"], tool_call_id: "activity-parent" },
      },
      {
        type: "agent_event",
        agent: "metric_expert",
        stage: "tool_start",
        status: "in_progress",
        payload: {
          delegated_expert: "metric",
          parent_tool_call_id: "activity-parent",
          tool: "query_memory_metrics",
          tool_call_id: "activity-metric-tool",
        },
      },
      {
        type: "tool_event",
        agent: "metric_expert",
        tool: "query_memory_metrics",
        status: "completed",
        payload: {
          delegated_expert: "metric",
          parent_tool_call_id: "activity-parent",
          tool_call_id: "activity-metric-tool",
        },
      },
    ];

    const { container } = render(<InlineAgentActivity run={run} />);
    const expert = container.querySelector('[data-activity-kind="expert"]');
    const expertBranch = expert?.closest(".inline-activity__step");

    expect(container.querySelectorAll(".inline-activity__body > .inline-activity__step")).toHaveLength(1);
    expect(expertBranch?.querySelector('[data-activity-kind="tool"]')).toBeInTheDocument();
  });
});
