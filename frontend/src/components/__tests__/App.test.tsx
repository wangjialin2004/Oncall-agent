import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import App from "../../App";

Element.prototype.scrollIntoView = vi.fn();

vi.mock("../../api/agentStream", () => ({
  streamAgent: vi.fn(async ({ onEvent }) => {
    onEvent({ type: "route_selected", route: "oncall", reason: "explicit_mode", mode: "oncall" });
    onEvent({
      type: "agent_event",
      agent: "triage",
      stage: "triage",
      status: "completed",
      summary: "incident structured",
      payload: {},
    });
    onEvent({ type: "report", route: "oncall", case_id: "case-1", report: "# report" });
    onEvent({ type: "complete", route: "oncall", answer: "# report", case_id: "case-1", events: [] });
  }),
}));

vi.mock("../../api/authApi", () => ({
  clearAuth: vi.fn(),
  loadAuth: vi.fn(() => ({ token: "test-token", username: "tester" })),
  logout: vi.fn(async () => {}),
}));

describe("App", () => {
  it("sends a message and renders realtime agent events", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(screen.getByLabelText("模式"), "oncall");
    await user.type(screen.getByLabelText("消息"), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("智能体过程")).toBeInTheDocument();
    expect(await screen.findByText("incident structured")).toBeInTheDocument();
    expect(await screen.findByText("已完成")).toBeInTheDocument();
    expect(await screen.findByText("case-1")).toBeInTheDocument();
    expect((await screen.findAllByText("# report")).length).toBeGreaterThan(0);
  });
});
