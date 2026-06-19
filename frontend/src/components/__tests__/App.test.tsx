import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../../App";

Element.prototype.scrollIntoView = vi.fn();

vi.mock("../../api/agentStream", () => ({
  streamAgent: vi.fn(async ({ onEvent }) => {
    onEvent({ type: "route_selected", route: "diagnosis", reason: "test_diagnosis", mode: "auto" });
    onEvent({
      type: "agent_event",
      agent: "diagnosis",
      stage: "start",
      status: "in_progress",
      summary: "综合诊断开始",
      payload: {},
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
  });

  it("sends a message and renders realtime agent events", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(screen.getByLabelText("模式"), "auto");
    await user.type(screen.getByLabelText("消息"), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("智能体过程")).toBeInTheDocument();
    expect(await screen.findByText("综合诊断开始")).toBeInTheDocument();
    expect(await screen.findByText("已完成")).toBeInTheDocument();
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
});
