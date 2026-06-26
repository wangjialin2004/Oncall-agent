import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../../App";
import { streamAgent } from "../../api/agentStream";

Element.prototype.scrollIntoView = vi.fn();

vi.mock("../../api/agentStream", () => ({
  streamAgent: vi.fn(async ({ onEvent }) => {
    onEvent({
      type: "route_selected",
      route: "diagnosis",
      reason: "test_diagnosis",
      mode: "auto",
      timelineEvent: {
        type: "route_event",
        agent: "router",
        route: "diagnosis",
        status: "completed",
        summary: "test_diagnosis",
      },
    });
    onEvent({
      type: "agent_event",
      agent: "diagnosis",
      stage: "start",
      status: "in_progress",
      summary: "综合诊断开始",
      payload: {},
    });
    onEvent({
      type: "agent_event",
      agent: "harness",
      stage: "plan",
      status: "completed",
      summary: "已生成调度计划",
      payload: {
        todos: ["确认目标", "选择工具"],
        required_evidence: ["指标曲线"],
        required_params: [{ name: "target", prompt: "服务名", reason: "指标查询需要目标" }],
      },
    });
    onEvent({
      type: "agent_event",
      agent: "harness",
      stage: "delegate_start",
      status: "in_progress",
      summary: "进入 log 专家处理子任务。",
      payload: {
        delegated_expert: "log",
        subtask: "查询 checkout-api 同时间段 ERROR 日志",
        tool_call_id: "call-log",
      },
    });
    onEvent({
      type: "tool_event",
      agent: "harness",
      tool: "get_service_ports_status",
      stage: "complete",
      status: "completed",
      summary: "端口状态已确认",
      payload: {
        arguments: { timezone: "Asia/Shanghai" },
        tool_call_id: "call-port-status",
      },
      duration_ms: 246.66,
      trace_id: "session-1782204557962-0740b1cf3bef",
      span_id: "tool-call-1",
    });
    onEvent({ type: "content", data: "诊断结论已确认" });
    onEvent({
      type: "agent_event",
      agent: "diagnosis",
      stage: "complete",
      status: "completed",
      summary: "综合诊断完成",
      payload: {},
    });
    onEvent({ type: "complete", route: "diagnosis", answer: "诊断结论已确认", case_id: "", events: [] });
  }),
}));

vi.mock("../../api/authApi", () => ({
  clearAuth: vi.fn(),
  loadAuth: vi.fn(() => ({ token: "test-token", username: "tester" })),
  logout: vi.fn(async () => {}),
}));

const mockSubmitFeedback = vi.fn(async (..._args: unknown[]) => "exp-1");
vi.mock("../../api/memoryApi", () => ({
  submitFeedback: (...args: unknown[]) => mockSubmitFeedback(...args),
}));

vi.mock("../../api/conversationApi", () => ({
  listConversations: vi.fn(async () => []),
  getConversation: vi.fn(async () => []),
  deleteConversation: vi.fn(async () => {}),
}));

describe("App", () => {
  afterEach(() => {
    cleanup();
    vi.mocked(streamAgent).mockClear();
  });

  it("sends a message and renders realtime agent events", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(screen.getByLabelText("模式"), "auto");
    await user.type(screen.getByLabelText("消息"), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("智能体过程")).toBeInTheDocument();
    expect(await screen.findByText("路由分发")).toBeInTheDocument();
    expect(await screen.findByText("综合诊断开始")).toBeInTheDocument();
    expect(await screen.findByText("已生成调度计划")).toBeInTheDocument();
    expect(await screen.findByText("进入专家：日志分析专家")).toBeInTheDocument();
    expect(await screen.findByText("进入子专家执行")).toBeInTheDocument();
    expect(await screen.findByText("工具执行")).toBeInTheDocument();
    expect(screen.getByText("工具执行").closest("summary")).not.toHaveTextContent("get_service_ports_status");
    for (const detailsToggle of screen.getAllByText("查看调度详情")) {
      await user.click(detailsToggle);
    }
    await user.click(screen.getByText("工具执行"));
    await user.click(screen.getAllByText("查看调度详情").at(-1)!);
    expect(await screen.findByText("计划步骤")).toBeInTheDocument();
    expect(await screen.findByText("确认目标")).toBeInTheDocument();
    expect(await screen.findByText("服务名：指标查询需要目标")).toBeInTheDocument();
    expect(await screen.findByText("查询 checkout-api 同时间段 ERROR 日志")).toBeInTheDocument();
    expect(await screen.findByText("工具名称")).toBeInTheDocument();
    expect(await screen.findByText("get_service_ports_status")).toBeInTheDocument();
    expect((await screen.findAllByText("已完成")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("诊断结论已确认")).length).toBeGreaterThan(0);
  });

  it("submits strong feedback when the user adopts a completed diagnosis", async () => {
    mockSubmitFeedback.mockClear();
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("消息"), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "发送" }));

    const adopt = await screen.findByRole("button", { name: "采纳" });
    await user.click(adopt);

    expect(mockSubmitFeedback).toHaveBeenCalledTimes(1);
    expect(mockSubmitFeedback.mock.calls[0][0]).toMatchObject({
      acceptanceLevel: "strong",
      userMessage: "checkout-api slow",
      assistantAnswer: "诊断结论已确认",
    });
    expect(await screen.findByText("已采纳，将沉淀为长期经验。")).toBeInTheDocument();
  });

  it("renders a complete answer when no content chunks arrive", async () => {
    vi.mocked(streamAgent).mockImplementationOnce(async ({ onEvent }) => {
      onEvent({
        type: "complete",
        route: "diagnosis",
        answer: "fallback answer",
        case_id: "",
        events: [],
      });
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByRole("textbox"), "hello");
    await user.keyboard("{Enter}");

    expect((await screen.findAllByText("fallback answer")).length).toBeGreaterThan(0);
  });
});
