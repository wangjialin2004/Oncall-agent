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
      summary: "事件已结构化",
      payload: {},
    });
    onEvent({ type: "report", route: "oncall", case_id: "case-1", report: "# 报告" });
    onEvent({ type: "complete", route: "oncall", answer: "# 报告", case_id: "case-1", events: [] });
  }),
}));

describe("App", () => {
  it("sends a message and renders realtime agent events", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(screen.getByLabelText("Agent mode"), "oncall");
    await user.type(screen.getByLabelText("Message"), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("智能体过程")).toBeInTheDocument();
    expect(await screen.findByText("事件已结构化")).toBeInTheDocument();
    expect(await screen.findByText("已完成")).toBeInTheDocument();
    expect(await screen.findByText("case-1")).toBeInTheDocument();
    expect((await screen.findAllByText("# 报告")).length).toBeGreaterThan(0);
  });
});
