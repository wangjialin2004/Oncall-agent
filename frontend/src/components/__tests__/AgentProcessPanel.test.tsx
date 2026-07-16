import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentRun, TimelineEvent } from "../../types/events";
import { AgentProcessPanel } from "../AgentProcessPanel";

function makeRun(status: AgentRun["status"], events: TimelineEvent[]): AgentRun {
  return {
    runId: "run-1",
    sessionId: "session-1",
    mode: "auto",
    route: "log",
    status,
    events,
    answer: "",
    caseId: "",
    error: "",
    userMessage: "分析日志",
    feedback: "",
  };
}

function planEvent(todos: string[]): TimelineEvent {
  return {
    type: "agent_event",
    agent: "harness",
    stage: "plan",
    status: "completed",
    summary: "Created lightweight investigation plan.",
    payload: { todos },
  };
}

describe("AgentProcessPanel tool result display", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  it("renders context_read evidence as human-readable summaries instead of raw JSON", () => {
    render(
      <AgentProcessPanel
        run={makeRun("running", [
          {
            type: "tool_event",
            agent: "harness",
            tool: "context_read",
            stage: "complete",
            status: "completed",
            summary: "读取上下文完成",
            payload: {
              arguments: { section: "evidence" },
              result: {
                section: "evidence",
                data: {
                  observed_facts: [],
                  tool_summaries: [
                    {
                      tool: "delegate_to_expert",
                      status: "success",
                      latency_ms: 67953,
                      note: JSON.stringify({
                        expert: "metric",
                        status: "completed",
                        subtask: "对 checkout-api 的 CPU 告警做初步排查",
                      }),
                    },
                  ],
                  evidence_gaps: [],
                  model_notes: [],
                },
              },
            },
          },
        ])}
      />,
    );

    expect(screen.getByText("分区").closest("div")).toHaveTextContent("分区证据");
    expect(screen.getByText("工具摘要").closest("div")).toHaveTextContent("工具摘要1 条");
    // Result row should humanize the nested delegate note, not dump JSON keys.
    expect(screen.getByText(/委派领域专家 ·/)).toBeInTheDocument();
    expect(screen.getByText(/对 checkout-api 的 CPU 告警做初步排查/)).toBeInTheDocument();
    expect(screen.queryByText(/"tool_summaries"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"observed_facts"/)).not.toBeInTheDocument();
  });

  it("renders delegate_to_expert result without dumping nested JSON", () => {
    render(
      <AgentProcessPanel
        run={makeRun("running", [
          {
            type: "tool_event",
            agent: "harness",
            tool: "delegate_to_expert",
            stage: "complete",
            status: "completed",
            summary: "专家委派完成",
            payload: {
              arguments: {
                expert: "metric",
                subtask: "查 checkout-api CPU",
              },
              result: {
                expert: "metric",
                status: "completed",
                subtask: "查 checkout-api CPU",
                answer: "当前无 firing 告警",
              },
            },
          },
        ])}
      />,
    );

    expect(screen.getByText("专家").closest("div")).toHaveTextContent("专家告警/指标专家");
    expect(screen.getByText("结论").closest("div")).toHaveTextContent("结论当前无 firing 告警");
    expect(screen.queryByText(/"expert": "metric"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"answer"/)).not.toBeInTheDocument();
  });
});

describe("AgentProcessPanel business steps", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  it("shows plan items as planned content without inferred completion", async () => {
    const user = userEvent.setup();
    const todos = [
      "理解当前请求、只读边界和可用历史上下文",
      "优先围绕日志分析焦点选择最相关工具取证：历史经验召回、服务知识查询、Redis 健康检查",
      "核对关键证据类型：错误日志样本、日志聚类或关键堆栈",
      "定稿前自检：结论是否由工具证据或历史上下文支撑",
    ];

    render(
      <AgentProcessPanel
        run={makeRun("completed", [
          planEvent(todos),
          {
            type: "agent_event",
            agent: "harness",
            stage: "complete",
            status: "completed",
            summary: "Unified Harness main loop completed.",
          },
        ])}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看制定排查计划详情" }));
    for (const todo of todos) {
      expect(screen.getByText(todo)).toBeVisible();
    }
    expect(screen.getByText("已规划 4 项")).toBeVisible();
    expect(document.querySelector(".plan-item")).not.toBeInTheDocument();
  });

  it("renders actual tool execution separately from the plan", () => {
    const todos = ["理解当前请求", "调用 search_app_logs 取证", "核对日志证据"];

    render(
      <AgentProcessPanel
        run={makeRun("running", [
          planEvent(todos),
          {
            type: "tool_event",
            agent: "harness",
            tool: "search_app_logs",
            stage: "complete",
            status: "completed",
            summary: "日志取证完成",
          },
        ])}
      />,
    );

    expect(document.querySelector(".process-step-row.phase-plan")).toHaveClass("status-success");
    expect(document.querySelector(".process-step-row.phase-execute")).toHaveClass("status-running");
    expect(screen.getAllByText("查询应用日志").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("2 个业务步骤")).toBeVisible();
  });

  it("preserves a user-expanded step when streaming data rerenders the panel", async () => {
    const user = userEvent.setup();
    const baseEvent: TimelineEvent = {
      type: "tool_event",
      agent: "harness",
      tool: "query_metrics",
      status: "completed",
      evidence_id: "call-1",
      payload: { result: { summary: "CPU 使用率恢复正常" } },
    };
    const { rerender } = render(<AgentProcessPanel run={makeRun("running", [baseEvent])} />);
    const collapse = screen.getByRole("button", { name: "收起查询指标详情" });
    await user.click(collapse);
    await user.click(screen.getByRole("button", { name: "查看查询指标详情" }));

    rerender(
      <AgentProcessPanel
        run={makeRun("running", [{ ...baseEvent, trace_id: "trace-1", duration_ms: 120 }])}
      />,
    );

    expect(screen.getByRole("button", { name: "收起查询指标详情" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("can fall back to the legacy event-card presentation", () => {
    vi.stubEnv("VITE_AGENT_PROCESS_PANEL_V2", "0");
    render(<AgentProcessPanel run={makeRun("running", [planEvent(["查询日志"])])} />);

    expect(screen.getByText("本轮概览")).toBeVisible();
    expect(screen.getByText("按步骤执行")).toBeVisible();
  });
});
