import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { AgentRun } from "../../../types/events";
import { AgentActivityFeed } from "../AgentActivityFeed";

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
      { type: "agent_event", stage: "plan", status: "completed" },
      {
        type: "agent_event",
        stage: "tool_start",
        status: "in_progress",
        payload: {
          tool: "query_prometheus_alerts",
          tool_call_id: "activity-1",
          arguments: { tenant: "SECRET_ARGUMENT" },
        },
      },
      {
        type: "agent_event",
        stage: "delegate_parallel_start",
        status: "in_progress",
        payload: {
          experts: ["metric", "log", "change"],
          tool_call_id: "activity-2",
          result: "SECRET_RESULT",
        },
      },
    ],
    answer: status === "completed" ? "final answer" : "",
    caseId: "",
    error: "",
    userMessage: "check cpu",
    feedback: "",
  };
}

describe("AgentActivityFeed", () => {
  it("renders each node as an independent expandable activity message", async () => {
    const user = userEvent.setup();
    const { container } = render(<AgentActivityFeed run={makeRun()} />);

    expect(container.querySelectorAll("[data-agent-activity-message]")).toHaveLength(7);
    expect(screen.queryByRole("button", { name: "正在排查 · 4 步 · 3 位专家 · 1 次工具调用" })).not.toBeInTheDocument();

    const routeToggle = screen.getByRole("button", { name: "展开已识别为综合诊断详细信息" });
    expect(routeToggle).toHaveAttribute("aria-expanded", "false");
    await user.click(routeToggle);

    expect(routeToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "已识别为综合诊断详细信息" })).toHaveTextContent(
      "判断问题类型，并选择本轮处理路径。",
    );
    expect(screen.getByRole("button", { name: "收起查询告警详细信息" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("updates a tool in place when its terminal event arrives", () => {
    const run = makeRun();
    const { container, rerender } = render(<AgentActivityFeed run={run} />);
    const before = container.querySelector('[data-activity-id="tool:activity-1"]');

    rerender(
      <AgentActivityFeed
        run={{
          ...run,
          events: [
            ...run.events,
            {
              type: "tool_event",
              tool: "query_prometheus_alerts",
              status: "completed",
              duration_ms: 1250,
              payload: { tool_call_id: "activity-1" },
            },
          ],
        }}
      />,
    );

    const after = container.querySelector('[data-activity-id="tool:activity-1"]');
    expect(after).toBe(before);
    expect(after).toHaveAttribute("data-activity-state", "completed");
    expect(after).toHaveTextContent("1.3 秒");
    expect(container.querySelectorAll('[data-activity-id="tool:activity-1"]')).toHaveLength(1);
  });

  it("renders a child Agent tool as a second-level activity message", () => {
    const run = makeRun();
    const { container } = render(
      <AgentActivityFeed
        run={{
          ...run,
          events: [
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
          ],
        }}
      />,
    );

    const expert = container.querySelector('[data-activity-id="experts:activity-parent:metric"]');
    const tool = container.querySelector('[data-activity-id="tool:activity-metric-tool"]');

    expect(expert).toHaveAttribute("data-activity-depth", "1");
    expect(tool).toHaveAttribute("data-activity-depth", "2");
    expect(expert?.compareDocumentPosition(tool as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("keeps private event fields out of the expandable details", () => {
    const { container } = render(<AgentActivityFeed run={makeRun()} />);

    expect(container).not.toHaveTextContent("SECRET_ARGUMENT");
    expect(container).not.toHaveTextContent("SECRET_RESULT");
    expect(container).not.toHaveTextContent("query_prometheus_alerts");
  });

  it("shows plan, tool result, and missing-evidence details in the feed", () => {
    const { container } = render(
      <AgentActivityFeed
        run={{
          ...makeRun("completed"),
          events: [
            {
              type: "agent_event",
              stage: "plan",
              status: "completed",
              payload: {
                todos: ["查询指标", "核对日志"],
                required_evidence: ["指标证据", "日志证据"],
              },
            },
            {
              type: "tool_event",
              tool: "query_metrics",
              status: "completed",
              payload: {
                tool_call_id: "activity-result",
                result_preview: "status=success；count=1",
                result_items: ["CPU 91%"],
              },
            },
            {
              type: "agent_event",
              stage: "verify",
              status: "degraded",
              payload: {
                confidence: "low",
                evidence_count: 1,
                failed_evidence_count: 0,
                gaps: ["缺少日志证据"],
              },
            },
          ],
        }}
      />,
    );

    expect(container).toHaveTextContent("1. 查询指标；2. 核对日志");
    expect(container).toHaveTextContent("status=success；count=1");
    expect(container).toHaveTextContent("CPU 91%");
    expect(container).toHaveTextContent("缺少日志证据");
  });
});
