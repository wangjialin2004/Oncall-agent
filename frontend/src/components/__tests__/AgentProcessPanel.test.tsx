import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentRun, TimelineEvent } from "../../types/events";
import { AgentProcessPanel } from "../AgentProcessPanel";

function makeRun(
  status: AgentRun["status"],
  events: TimelineEvent[],
  overrides: Partial<AgentRun> = {},
): AgentRun {
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
    ...overrides,
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

  it("merges pending distill draft and diagnosis feedback into one card", async () => {
    const onFeedback = vi.fn();
    const onDistill = vi.fn();
    const user = userEvent.setup();
    render(
      <AgentProcessPanel
        run={makeRun("completed", [], {
          answer: "诊断结论已确认",
          distillDraft: {
            experience_id: "exp-draft-1",
            status: "pending",
            enabled: true,
            requires_confirm: true,
          },
        })}
        onFeedback={onFeedback}
        onDistill={onDistill}
      />,
    );

    // One unified card — no stacked "这次诊断有帮助吗？" duplicate.
    expect(screen.getAllByTestId("feedback-card")).toHaveLength(1);
    expect(screen.getByText(/待确认经验草稿/)).toBeInTheDocument();
    expect(screen.queryByText("这次诊断有帮助吗？")).not.toBeInTheDocument();
    expect(screen.queryByText("自动蒸馏草稿")).not.toBeInTheDocument();

    expect(screen.getByRole("button", { name: "采纳为经验" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "纠正" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝草稿" })).toBeInTheDocument();
    // The bare "采纳" / "拒绝" buttons from the old dual-card layout must be gone.
    expect(screen.queryByRole("button", { name: "采纳" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "采纳为经验" }));
    expect(onDistill).toHaveBeenCalledWith("confirm");
    expect(onFeedback).not.toHaveBeenCalled();
  });

  it("shows a single settled card after distill is confirmed", () => {
    render(
      <AgentProcessPanel
        run={makeRun("completed", [], {
          answer: "诊断结论已确认",
          distillDraft: {
            experience_id: "exp-draft-1",
            status: "pending",
            enabled: true,
            requires_confirm: true,
          },
          distillStatus: "confirmed",
          feedback: "adopted",
        })}
      />,
    );

    expect(screen.getAllByTestId("feedback-card")).toHaveLength(1);
    expect(screen.getByText("已采纳，将沉淀为长期经验。")).toBeInTheDocument();
    expect(screen.queryByText("已确认采纳经验草稿（不会执行任何变更）。")).not.toBeInTheDocument();
  });

  it("renders suggested actions and confirms audit-only HITL actions", async () => {
    const onConfirmSuggestion = vi.fn();
    const user = userEvent.setup();
    render(
      <AgentProcessPanel
        run={makeRun("completed", [], {
          answer: "诊断结论已确认",
          suggestedActions: [
            {
              id: "review_metrics",
              title: "建议：复核相关指标/告警时间窗（只读）",
              risk: "low",
              requires_confirm: true,
            },
            {
              id: "review_logs",
              title: "建议：抽样核对错误日志与变更窗口（只读）",
              risk: "low",
              requires_confirm: true,
            },
          ],
        })}
        onConfirmSuggestion={onConfirmSuggestion}
      />,
    );

    expect(screen.getByTestId("suggested-actions-card")).toBeInTheDocument();
    expect(screen.getByText(/不会自动执行重启/)).toBeInTheDocument();

    const buttons = screen.getAllByRole("button", { name: "确认建议" });
    expect(buttons).toHaveLength(2);
    await user.click(buttons[0]);
    expect(onConfirmSuggestion).toHaveBeenCalledWith("review_metrics");
  });

  it("marks confirmed suggested actions as audit-only", () => {
    render(
      <AgentProcessPanel
        run={makeRun("completed", [], {
          answer: "诊断结论已确认",
          suggestedActions: [
            {
              id: "review_metrics",
              title: "建议：复核相关指标/告警时间窗（只读）",
              risk: "low",
              requires_confirm: true,
            },
          ],
          confirmedActionIds: ["review_metrics"],
        })}
      />,
    );

    expect(screen.getByText("已确认（未执行）")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认建议" })).not.toBeInTheDocument();
    expect(screen.getByText(/全部建议已确认记录/)).toBeInTheDocument();
  });
});
