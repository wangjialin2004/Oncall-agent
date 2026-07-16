import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
