import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentRun } from "../../types/events";
import { ChatWorkspace } from "../ChatWorkspace";

afterEach(cleanup);

function activityRun(): AgentRun {
  return {
    runId: "assistant-activity",
    sessionId: "session-1",
    mode: "auto",
    route: "diagnosis",
    status: "completed",
    events: [
      { type: "route_event", route: "diagnosis", status: "completed" },
      {
        type: "agent_event",
        stage: "tool_start",
        status: "in_progress",
        payload: { tool: "query_metrics", tool_call_id: "activity-1" },
      },
      {
        type: "tool_event",
        tool: "query_metrics",
        status: "completed",
        duration_ms: 900,
        payload: { tool_call_id: "activity-1" },
      },
    ],
    answer: "最终结论",
    caseId: "",
    error: "",
    userMessage: "check cpu",
    feedback: "",
  };
}

describe("ChatWorkspace", () => {
  it("renders granular activities before a separate final answer bubble", () => {
    const run = activityRun();
    const { container } = render(
      <ChatWorkspace
        mode="auto"
        messages={[{ id: run.runId, role: "assistant", content: "## 最终结论" }]}
        runs={{ [run.runId]: run }}
        runStatus="idle"
        pendingAttachments={[]}
        checkpointReplay={false}
        onCheckpointReplayChange={vi.fn()}
        onModeChange={vi.fn()}
        onSend={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onUploadFile={vi.fn(async () => ({ fileId: "file_1", fileName: "runbook.md", deduplicated: false }))}
        onStop={vi.fn()}
      />,
    );

    const activity = container.querySelector("[data-agent-activity-message]");
    const answerBubble = container.querySelector(".message.assistant .message-bubble");
    expect(activity).not.toBeNull();
    expect(answerBubble).not.toBeNull();
    if (!activity || !answerBubble) throw new Error("activity and answer should both render");
    expect(answerBubble.contains(activity)).toBe(false);
    expect(
      activity.compareDocumentPosition(answerBubble) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "执行轨迹" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "最终结论" })).toBeInTheDocument();
  });

  it("does not render an empty final answer bubble while activities are running", () => {
    const run = { ...activityRun(), status: "running" as const, answer: "" };
    const { container } = render(
      <ChatWorkspace
        mode="auto"
        messages={[{ id: run.runId, role: "assistant", content: "" }]}
        runs={{ [run.runId]: run }}
        runStatus="running"
        pendingAttachments={[]}
        checkpointReplay={false}
        onCheckpointReplayChange={vi.fn()}
        onModeChange={vi.fn()}
        onSend={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onUploadFile={vi.fn(async () => ({ fileId: "file_1", fileName: "runbook.md", deduplicated: false }))}
        onStop={vi.fn()}
      />,
    );

    expect(container.querySelector("[data-agent-activity-message]")).not.toBeNull();
    expect(container.querySelector(".message.assistant .message-bubble")).toBeNull();
  });

  it("restores the aggregate inline card when granular activities are disabled", () => {
    const run = activityRun();
    render(
      <ChatWorkspace
        mode="auto"
        messages={[{ id: run.runId, role: "assistant", content: "最终结论" }]}
        runs={{ [run.runId]: run }}
        granularActivityEnabled={false}
        runStatus="idle"
        pendingAttachments={[]}
        checkpointReplay={false}
        onCheckpointReplayChange={vi.fn()}
        onModeChange={vi.fn()}
        onSend={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onUploadFile={vi.fn(async () => ({ fileId: "file_1", fileName: "runbook.md", deduplicated: false }))}
        onStop={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /已完成/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "执行轨迹" })).not.toBeInTheDocument();
  });

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
        pendingAttachments={[]}
        checkpointReplay={false}
        onCheckpointReplayChange={vi.fn()}
        onModeChange={vi.fn()}
        onSend={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onUploadFile={vi.fn(async () => ({ fileId: "file_1", fileName: "runbook.md", deduplicated: false }))}
        onStop={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "诊断结论" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("checkout-api")).toBeInTheDocument();
  });

  it("hides leaked tool protocol from persisted assistant history", () => {
    render(
      <ChatWorkspace
        mode="auto"
        messages={[
          {
            id: "assistant-leaked",
            role: "assistant",
            content: [
              "> 鈿狅笍 璇佹嵁鑷检锛氱疆淇″害 medium锛屾湰娆″洖绛斿瓨鍦ㄤ互涓嬭瘉鎹己鍙�",
              "> - 4 个工具调用失败或被降级",
              "",
              "我先补一次日志证据。 to=multi_tool_use.parallel 乐彩广告",
              '{"tool_uses":[{"recipient_name":"functions.search_app_logs","parameters":{"keyword":"OOM"}}]}## 现象',
              "`payment-service` 内存使用率超过 85%。",
            ].join("\n"),
          },
        ]}
        runStatus="idle"
        pendingAttachments={[]}
        checkpointReplay={false}
        onCheckpointReplayChange={vi.fn()}
        onModeChange={vi.fn()}
        onSend={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onUploadFile={vi.fn(async () => ({ fileId: "file_1", fileName: "runbook.md", deduplicated: false }))}
        onStop={vi.fn()}
      />,
    );

    expect(screen.getByText("现象")).toBeInTheDocument();
    expect(screen.getByText("payment-service")).toBeInTheDocument();
    expect(screen.getByText(/证据自检：置信度 medium/)).toBeInTheDocument();
    expect(screen.queryByText(/tool_uses/)).not.toBeInTheDocument();
    expect(screen.queryByText(/recipient_name/)).not.toBeInTheDocument();
    expect(screen.queryByText(/鈿/)).not.toBeInTheDocument();
  });

  it("uploads a selected file and renders success feedback", async () => {
    const user = userEvent.setup();
    const onUploadFile = vi.fn(async () => ({
      fileId: "file_incident",
      fileName: "incident.md",
      deduplicated: false,
    }));

    const { container } = render(
      <ChatWorkspace
        mode="auto"
        messages={[]}
        runStatus="idle"
        pendingAttachments={[]}
        checkpointReplay={false}
        onCheckpointReplayChange={vi.fn()}
        onModeChange={vi.fn()}
        onSend={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onUploadFile={onUploadFile}
        onStop={vi.fn()}
      />,
    );

    const input = container.querySelector('input[type="file"]') as HTMLInputElement | null;
    const file = new File(["hello"], "incident.md", { type: "text/markdown" });

    expect(input).not.toBeNull();
    await user.upload(input as HTMLInputElement, file);

    expect(onUploadFile).toHaveBeenCalledWith(file);
    expect(await screen.findByText("已添加附件：incident.md")).toBeInTheDocument();
    await waitFor(
      () => {
        expect(screen.queryByText("已添加附件：incident.md")).not.toBeInTheDocument();
      },
      { timeout: 2000 },
    );
  });
});
