import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../../App";
import type { CheckpointSummary } from "../../api/checkpointApi";
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
      tool: "search_app_logs",
      stage: "complete",
      status: "completed",
      summary: "端口状态已确认",
      payload: {
        arguments: { keyword: "redis", level: "WARN", limit: 50 },
        result:
          '{"status":"success","source":"local_logs","logs":[],"total":0,"query":{"keyword":"redis","level":"WARN","limit":50}}',
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
  AuthError: class AuthError extends Error {
    code: number;
    constructor(code: number, message: string) {
      super(message);
      this.name = "AuthError";
      this.code = code;
    }
  },
  clearAuth: vi.fn(),
  fetchMe: vi.fn(async () => ({ username: "tester", ownerKey: "owner1" })),
  loadAuth: vi.fn(() => ({ token: "test-token", username: "tester" })),
  logout: vi.fn(async () => {}),
}));

const mockSubmitFeedback = vi.fn(async (..._args: unknown[]) => "exp-1");
vi.mock("../../api/memoryApi", () => ({
  submitFeedback: (...args: unknown[]) => mockSubmitFeedback(...args),
  confirmDistillDraft: vi.fn(async () => "exp-1"),
  rejectDistillDraft: vi.fn(async () => "exp-1"),
}));

const mockConfirmSuggestion = vi.fn(async (_args: unknown) => ({
  accepted: true,
  executed: false,
  hint: "确认已记录；系统不会自动执行变更/重启/回滚。",
}));
vi.mock("../../api/hitlApi", () => ({
  confirmSuggestion: (args: unknown) => mockConfirmSuggestion(args),
}));

const mockGetCheckpoint = vi.fn<(sessionId: string) => Promise<CheckpointSummary>>(async (sessionId) => ({
  sessionId,
  enabled: false,
  resumable: false,
}));
const mockDeleteCheckpoint = vi.fn<(sessionId: string) => Promise<number>>(async () => 0);
vi.mock("../../api/checkpointApi", () => ({
  getCheckpoint: (sessionId: string) => mockGetCheckpoint(sessionId),
  deleteCheckpoint: (sessionId: string) => mockDeleteCheckpoint(sessionId),
}));

vi.mock("../../api/conversationApi", () => ({
  listConversations: vi.fn(async () => []),
  getConversation: vi.fn(async () => []),
  deleteConversation: vi.fn(async () => {}),
}));

async function waitForChatReady() {
  await waitFor(() => {
    expect(screen.queryByText("正在校验登录状态…")).not.toBeInTheDocument();
  });
  return screen.getByLabelText("消息");
}

describe("App", () => {
  afterEach(() => {
    cleanup();
    vi.mocked(streamAgent).mockClear();
    mockGetCheckpoint.mockReset();
    mockDeleteCheckpoint.mockReset();
  });

  it("sends a message and renders realtime agent events", async () => {
    const user = userEvent.setup();
    const { getConversation } = await import("../../api/conversationApi");
    render(<App />);
    // Wait for the mount-time session hydrate so it cannot wipe streamed events.
    await waitFor(() => expect(vi.mocked(getConversation)).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByLabelText("消息")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("模式"), "auto");
    await user.type(screen.getByLabelText("消息"), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("智能体过程")).toBeInTheDocument();
    expect(await screen.findByTestId("process-chain")).toBeInTheDocument();
    expect(await screen.findByText("执行链路")).toBeInTheDocument();
    expect(await screen.findByLabelText("本轮过程摘要")).toBeInTheDocument();

    await waitFor(() => {
      const body = document.body.textContent || "";
      expect(body.includes("识别请求") || body.includes("诊断路径")).toBe(true);
      expect(body.includes("制定排查计划")).toBe(true);
      expect(body.includes("查询应用日志") || body.includes("端口状态已确认")).toBe(true);
      expect(body.includes("进入 log 专家")).toBe(false);
      expect(body.includes("Created lightweight")).toBe(false);
      expect(body.includes("search_app_logs")).toBe(false);
    });

    await user.click(await screen.findByRole("button", { name: "查看制定排查计划详情" }));
    await user.click(await screen.findByRole("button", { name: "查看查询应用日志详情" }));

    expect(await screen.findByText("确认目标")).toBeInTheDocument();
    expect(await screen.findByText("委派日志分析专家")).toBeInTheDocument();
    expect(await screen.findByText("服务名：指标查询需要目标")).toBeInTheDocument();
    expect(await screen.findByText("查询 checkout-api 同时间段 ERROR 日志")).toBeInTheDocument();
    expect(await screen.findByText("端口状态已确认")).toBeInTheDocument();
    expect(await screen.findByText("关键词")).toBeInTheDocument();
    expect(await screen.findByText("redis")).toBeInTheDocument();
    expect(await screen.findByText("关键结果")).toBeInTheDocument();

    // Overview shows actual unique activity counters.
    expect(await screen.findByTitle("工具调用次数")).toHaveTextContent("1 次工具");
    expect(await screen.findByTitle("专家委派次数")).toHaveTextContent("1 次委派");
    expect(await screen.findByTitle("有效证据数量")).toHaveTextContent("1 条证据");

    // Technical IDs stay hidden.
    expect(screen.queryByText("Trace")).not.toBeInTheDocument();
    expect(screen.queryByText("Span")).not.toBeInTheDocument();
    expect(screen.queryByText(/eval-M1-two-turn/)).not.toBeInTheDocument();
    expect(screen.queryByText(/session-1782204557962/)).not.toBeInTheDocument();

    expect((await screen.findAllByText("已完成")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("诊断结论已确认")).length).toBeGreaterThan(0);
  });

  it("submits strong feedback when the user adopts a completed diagnosis", async () => {
    mockSubmitFeedback.mockClear();
    const user = userEvent.setup();
    render(<App />);

    await user.type(await waitForChatReady(), "checkout-api slow");
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

  it("confirms suggested actions via the HITL audit API without executing them", async () => {
    mockConfirmSuggestion.mockClear();
    vi.mocked(streamAgent).mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ type: "content", data: "诊断结论已确认" });
      onEvent({
        type: "complete",
        route: "diagnosis",
        answer: "诊断结论已确认",
        case_id: "",
        events: [],
        suggested_actions: [
          {
            id: "review_metrics",
            title: "建议：复核相关指标/告警时间窗（只读）",
            risk: "low",
            requires_confirm: true,
          },
        ],
      });
    });

    const user = userEvent.setup();
    render(<App />);

    await user.type(await waitForChatReady(), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByTestId("suggested-actions-card")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认建议" }));

    await waitFor(() => {
      expect(mockConfirmSuggestion).toHaveBeenCalledWith({
        sessionId: expect.any(String),
        actionId: "review_metrics",
        note: "panel-confirm",
      });
    });
    expect(await screen.findByText("已确认（未执行）")).toBeInTheDocument();
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

    await user.type(await waitForChatReady(), "hello");
    await user.keyboard("{Enter}");

    expect((await screen.findAllByText("fallback answer")).length).toBeGreaterThan(0);
  });

  it("replaces a provisional streamed answer with the canonical complete answer", async () => {
    vi.mocked(streamAgent).mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ type: "content", data: "provisional answer" });
      onEvent({
        type: "complete",
        route: "diagnosis",
        answer: "verified replacement answer",
        replace_streamed_answer: true,
        case_id: "",
        events: [],
      });
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(await waitForChatReady(), "check cpu");
    await user.keyboard("{Enter}");

    expect(await screen.findByText("verified replacement answer")).toBeInTheDocument();
    expect(screen.queryByText("provisional answer")).not.toBeInTheDocument();
  });

  it("keeps legacy appended content when replacement is not requested", async () => {
    vi.mocked(streamAgent).mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ type: "content", data: "initial answer" });
      onEvent({ type: "content", data: " replacement answer" });
      onEvent({
        type: "complete",
        route: "diagnosis",
        answer: "replacement answer",
        replace_streamed_answer: false,
        case_id: "",
        events: [],
      });
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(await waitForChatReady(), "check cpu");
    await user.keyboard("{Enter}");

    expect(await screen.findByText("initial answer replacement answer")).toBeInTheDocument();
  });

  it("shows a checkpoint_resume banner when the harness reports a resumed run", async () => {
    vi.mocked(streamAgent).mockImplementationOnce(async ({ onEvent }) => {
      onEvent({
        type: "checkpoint_resume",
        resumedFromStep: 2,
        replayedSteps: 3,
        startedAt: "2026-07-05T00:00:00Z",
        conservative: true,
        replayOverride: false,
      });
      onEvent({
        type: "agent_event",
        agent: "harness",
        stage: "model_decision",
        status: "in_progress",
        summary: "resumed step 3",
        payload: {},
      });
      onEvent({
        type: "complete",
        route: "metric",
        answer: "resumed answer",
        case_id: "",
        events: [],
      });
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(await waitForChatReady(), "resume me");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByTestId("checkpoint-resume-banner")).toBeInTheDocument();
    expect(screen.getByText(/强制保守：本次明确拒绝重放非白名单工具/)).toBeInTheDocument();
    expect(screen.getByText("激进恢复")).toBeInTheDocument();
  });

  it("renders both resume and conservative close banners when the harness skips replay", async () => {
    vi.mocked(streamAgent).mockImplementationOnce(async ({ onEvent }) => {
      onEvent({
        type: "checkpoint_resume",
        resumedFromStep: 2,
        replayedSteps: 3,
        startedAt: "",
        conservative: true,
        replayOverride: null,
      });
      onEvent({
        type: "checkpoint_conservative_close",
        step: 2,
      });
      onEvent({
        type: "complete",
        route: "metric",
        answer: "closed without replay",
        case_id: "",
        events: [],
      });
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(await waitForChatReady(), "resume conservatively");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByTestId("checkpoint-resume-banner")).toBeInTheDocument();
    expect(await screen.findByTestId("checkpoint-conservative-close-banner")).toBeInTheDocument();
  });

  it("marks sidebar sessions with a resumable badge when a checkpoint exists", async () => {
    const { listConversations, getConversation } = await import("../../api/conversationApi");
    vi.mocked(listConversations).mockResolvedValueOnce([
      {
        session_id: "sid-resumable",
        title: "上次没排完",
        created_at: "",
        updated_at: "",
        turn_count: 2,
      },
    ]);
    vi.mocked(getConversation).mockResolvedValueOnce([]);
    mockGetCheckpoint.mockResolvedValueOnce({
      sessionId: "sid-resumable",
      enabled: true,
      resumable: true,
      step: 3,
    });

    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("上次没排完")).toBeInTheDocument();
    expect(await screen.findByText("可继续")).toBeInTheDocument();
    expect(mockGetCheckpoint).toHaveBeenCalledWith("sid-resumable");
  });
});
