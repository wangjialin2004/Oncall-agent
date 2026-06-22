import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatWorkspace } from "../ChatWorkspace";

describe("ChatWorkspace", () => {
  it("renders assistant Markdown with GFM tables and code", () => {
    render(
      <ChatWorkspace
        mode="auto"
        messages={[
          {
            id: "assistant-1",
            role: "assistant",
            content: [
              "## 诊断结论",
              "",
              "| 指标 | 数值 |",
              "| --- | ---: |",
              "| P95 | 280ms |",
              "",
              "`checkout-api` 延迟升高",
            ].join("\n"),
          },
        ]}
        runStatus="idle"
        onModeChange={vi.fn()}
        onSend={vi.fn()}
        onStop={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "诊断结论" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("checkout-api")).toBeInTheDocument();
  });
});
