import { Activity, LoaderCircle, Paperclip, Search, Send, Square, X } from "lucide-react";
import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AgentMode, ChatMessage, RunStatus } from "../types/events";
import { sanitizeAssistantContent } from "../utils/assistantContent";

export type PendingAttachment = {
  fileId: string;
  fileName: string;
};

type ChatWorkspaceProps = {
  mode: AgentMode;
  messages: ChatMessage[];
  runStatus: RunStatus;
  pendingAttachments: PendingAttachment[];
  selectedId?: string;
  /** Whether the next send should request aggressive checkpoint replay. */
  checkpointReplay: boolean;
  onCheckpointReplayChange: (value: boolean) => void;
  /** W10: structured missing params for quick-fill chips. */
  clarifyChips?: string[];
  onModeChange: (mode: AgentMode) => void;
  onSend: (message: string) => void;
  onRemoveAttachment: (fileId: string) => void;
  onUploadFile: (file: File) => Promise<{ fileId: string; fileName: string; deduplicated: boolean }>;
  onSelectMessage?: (id: string) => void;
  onStop: () => void;
};

export function ChatWorkspace({
  mode,
  messages,
  runStatus,
  pendingAttachments,
  selectedId,
  checkpointReplay,
  onCheckpointReplayChange,
  clarifyChips = [],
  onModeChange,
  onSend,
  onRemoveAttachment,
  onUploadFile,
  onSelectMessage,
  onStop,
}: ChatWorkspaceProps) {
  const [message, setMessage] = useState("");
  const [uploadState, setUploadState] = useState<"idle" | "uploading" | "success" | "error">("idle");
  const [uploadMessage, setUploadMessage] = useState("");
  const isRunning = runStatus === "running";
  const messagesRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const successResetTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const messagesEl = messagesRef.current;
    if (!messagesEl) {
      return;
    }
    if (typeof messagesEl.scrollTo === "function") {
      messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: "smooth" });
      return;
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }, [messages]);

  useEffect(() => {
    return () => {
      if (successResetTimerRef.current !== null) {
        window.clearTimeout(successResetTimerRef.current);
      }
    };
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || isRunning) {
      return;
    }
    setMessage("");
    onSend(trimmed);
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    event.target.value = "";
    if (!selected || isRunning || uploadState === "uploading") {
      return;
    }

    setUploadState("uploading");
    setUploadMessage(`正在上传 ${selected.name}...`);

    try {
      const result = await onUploadFile(selected);
      setUploadState("success");
      setUploadMessage(
        result.deduplicated
          ? `已添加附件：${result.fileName}（复用已有文件）`
          : `已添加附件：${result.fileName}`,
      );
      if (successResetTimerRef.current !== null) {
        window.clearTimeout(successResetTimerRef.current);
      }
      successResetTimerRef.current = window.setTimeout(() => {
        setUploadState("idle");
        setUploadMessage("");
        successResetTimerRef.current = null;
      }, 900);
    } catch (error) {
      setUploadState("error");
      setUploadMessage(error instanceof Error ? error.message : "文件上传失败");
    }
  }

  function triggerFilePicker() {
    if (isRunning || uploadState === "uploading") {
      return;
    }
    fileInputRef.current?.click();
  }

  return (
    <section className="chat-workspace">
      <header className="chat-header">
        <div>
          <h2>运维助手</h2>
          <p>
            {isRunning ? (
              <>
                <span className="header-dot is-running" aria-hidden="true" />
                正在执行智能体推理...
              </>
            ) : (
              "就绪 · 智能 OnCall 运维平台"
            )}
          </p>
        </div>
        <label className="mode-select">
          <span>模式</span>
          <select value={mode} onChange={(event) => onModeChange(event.target.value as AgentMode)}>
            <option value="auto">自动</option>
            <option value="rag">知识库</option>
          </select>
        </label>
        <label
          className="replay-toggle"
          title="勾选后，下一次 send 会让后端重放 checkpoint 中的非白名单工具（包括可能的副作用）。"
        >
          <input
            type="checkbox"
            checked={checkpointReplay}
            onChange={(event) => onCheckpointReplayChange(event.target.checked)}
            disabled={isRunning}
          />
          <span>激进恢复</span>
        </label>
      </header>

      <div className={`messages${isRunning ? " is-running" : ""}`} ref={messagesRef}>
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">
              <Activity size={24} aria-hidden="true" />
            </div>
            <strong className="empty-state-title">运维助手已就绪</strong>
            <span className="empty-state-hint">描述一个告警事件，或向知识库提问</span>
          </div>
        ) : (
          messages.map((item) => {
            const selectable = item.role === "assistant" && Boolean(onSelectMessage);
            const isSelected = selectable && item.id === selectedId;
            return (
              <article
                className={`message ${item.role}${isSelected ? " selected" : ""}`}
                key={item.id}
              >
                <div
                  className="message-bubble"
                  {...(selectable
                    ? {
                        role: "button",
                        tabIndex: 0,
                        "aria-pressed": isSelected,
                        title: "查看该回合的智能体过程",
                        onClick: () => onSelectMessage?.(item.id),
                        onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            onSelectMessage?.(item.id);
                          }
                        },
                      }
                    : {})}
                >
                  {item.role === "assistant" ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {sanitizeAssistantContent(item.content)}
                    </ReactMarkdown>
                  ) : (
                    item.content
                  )}
                </div>
              </article>
            );
          })
        )}
      </div>

      <form className="composer" onSubmit={submit}>
        <input
          ref={fileInputRef}
          className="sr-only"
          type="file"
          tabIndex={-1}
          onChange={handleFileChange}
        />
        <label className="sr-only" htmlFor="message-input">
          消息
        </label>
        {clarifyChips.length > 0 && !isRunning ? (
          <div className="clarify-chips" role="group" aria-label="澄清快捷填槽">
            {clarifyChips.map((param) => (
              <button
                key={param}
                type="button"
                className="ghost clarify-chip"
                onClick={() => {
                  const snippet = `${param}=`;
                  setMessage((current) => (current.includes(snippet) ? current : `${current}${current ? " " : ""}${snippet}`.trimStart()));
                }}
              >
                {param}
              </button>
            ))}
          </div>
        ) : null}
        <button
          className="icon-button"
          type="button"
          onClick={triggerFilePicker}
          aria-label="上传文件"
          title="上传文件"
          disabled={isRunning || uploadState === "uploading"}
        >
          {uploadState === "uploading" ? (
            <LoaderCircle size={17} className="spin" aria-hidden="true" />
          ) : (
            <Paperclip size={17} aria-hidden="true" />
          )}
        </button>
        <div className="composer-input-wrap">
          <Search size={14} className="composer-input-icon" aria-hidden="true" />
          <input
            id="message-input"
            aria-label="消息"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="描述告警事件或提出运维问题..."
            disabled={isRunning}
          />
        </div>
        {isRunning ? (
          <button className="icon-button" type="button" onClick={onStop} aria-label="停止">
            <Square size={17} aria-hidden="true" />
          </button>
        ) : (
          <button className="icon-button primary" type="submit" aria-label="发送">
            <Send size={17} aria-hidden="true" />
          </button>
        )}
        {pendingAttachments.length > 0 ? (
          <div className="composer-attachments" aria-label="待发送附件">
            {pendingAttachments.map((attachment) => (
              <span className="attachment-chip" key={attachment.fileId}>
                <Paperclip size={12} aria-hidden="true" />
                <span>{attachment.fileName}</span>
                <button
                  type="button"
                  className="attachment-chip-remove"
                  aria-label={`移除附件 ${attachment.fileName}`}
                  onClick={() => onRemoveAttachment(attachment.fileId)}
                  disabled={isRunning}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        {uploadState !== "idle" && uploadMessage ? (
          <p className={`composer-status ${uploadState}`} role={uploadState === "error" ? "alert" : "status"}>
            {uploadMessage}
          </p>
        ) : null}
      </form>
    </section>
  );
}
