import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../../App";

Element.prototype.scrollIntoView = vi.fn();

const { mockStreamAgent, mockListConversations, mockGetConversation, mockDeleteConversation } =
  vi.hoisted(() => ({
    mockStreamAgent: vi.fn(),
    mockListConversations: vi.fn(),
    mockGetConversation: vi.fn(),
    mockDeleteConversation: vi.fn(),
  }));

vi.mock("../../api/agentStream", () => ({
  streamAgent: (...args: unknown[]) => mockStreamAgent(...args),
}));

vi.mock("../../api/authApi", () => ({
  clearAuth: vi.fn(),
  loadAuth: vi.fn(() => ({ token: "test-token", username: "tester" })),
  logout: vi.fn(async () => {}),
}));

vi.mock("../../api/memoryApi", () => ({
  submitFeedback: vi.fn(async () => "exp-1"),
}));

vi.mock("../../api/conversationApi", () => ({
  listConversations: (...args: unknown[]) => mockListConversations(...args),
  getConversation: (...args: unknown[]) => mockGetConversation(...args),
  deleteConversation: (...args: unknown[]) => mockDeleteConversation(...args),
}));

describe("multi-turn conversation UI", () => {
  beforeEach(() => {
    localStorage.clear();
    mockStreamAgent.mockReset();
    mockListConversations.mockReset().mockResolvedValue([]);
    mockGetConversation.mockReset().mockResolvedValue([]);
    mockDeleteConversation.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
  });

  it("streams assistant content into the chat bubble as it arrives", async () => {
    // Emit content chunks but no `complete` — proving content reaches the bubble itself,
    // not only the final answer.
    mockStreamAgent.mockImplementation(async ({ onEvent }: { onEvent: (e: unknown) => void }) => {
      onEvent({ type: "route_selected", route: "metric", reason: "test", mode: "auto" });
      onEvent({ type: "content", data: "实时" });
      onEvent({ type: "content", data: "输出" });
    });

    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("消息"), "看下指标");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // The streamed text shows in the chat bubble (and also the side-panel report).
    expect((await screen.findAllByText("实时输出")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Running...")).not.toBeInTheDocument();
  });

  it("lists past conversations and restores one when clicked", async () => {
    mockListConversations.mockResolvedValue([
      {
        session_id: "s-old",
        title: "历史问题",
        created_at: "2026-06-18T00:00:00Z",
        updated_at: "2026-06-18T00:00:00Z",
        turn_count: 1,
      },
    ]);
    mockGetConversation.mockImplementation(async (sid: string) =>
      sid === "s-old"
        ? [
            {
              turn_index: 0,
              user_message: "旧的问题",
              assistant_answer: "旧的回答",
              route: "metric",
              case_id: "",
              events: [],
              created_at: "2026-06-18T00:00:00Z",
            },
          ]
        : [],
    );

    const user = userEvent.setup();
    render(<App />);

    // The current (fresh) session is empty; the sidebar still shows the old one.
    const entry = await screen.findByTitle("历史问题");
    await user.click(entry);

    expect(await screen.findByText("旧的问题")).toBeInTheDocument();
    expect((await screen.findAllByText("旧的回答")).length).toBeGreaterThan(0);
  });

  it("deletes a conversation from the sidebar", async () => {
    mockListConversations.mockResolvedValue([
      {
        session_id: "s-old",
        title: "历史问题",
        created_at: "2026-06-18T00:00:00Z",
        updated_at: "2026-06-18T00:00:00Z",
        turn_count: 1,
      },
    ]);

    const user = userEvent.setup();
    render(<App />);

    await screen.findByTitle("历史问题");
    await user.click(screen.getByRole("button", { name: "删除会话 历史问题" }));

    expect(mockDeleteConversation).toHaveBeenCalledWith("s-old");
  });
});
