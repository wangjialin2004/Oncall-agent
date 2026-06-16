import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import App from "../../App";

vi.mock("../../api/agentStream", () => ({
  streamAgent: vi.fn(async ({ onEvent }) => {
    onEvent({ type: "route_selected", route: "oncall", reason: "explicit_mode", mode: "oncall" });
    onEvent({
      type: "agent_event",
      agent: "triage",
      stage: "triage",
      status: "completed",
      summary: "Incident structured",
      payload: {},
    });
    onEvent({ type: "report", route: "oncall", case_id: "case-1", report: "# Report" });
    onEvent({ type: "complete", route: "oncall", answer: "# Report", case_id: "case-1", events: [] });
  }),
}));

describe("App", () => {
  it("sends a message and renders realtime agent events", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(screen.getByLabelText("Agent mode"), "oncall");
    await user.type(screen.getByLabelText("Message"), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Incident structured")).toBeInTheDocument();
    expect(await screen.findByText("case-1")).toBeInTheDocument();
    expect((await screen.findAllByText("# Report")).length).toBeGreaterThan(0);
  });
});
