import { Activity, Send, Square } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AgentMode, ChatMessage, RunStatus } from "../types/events";

type ChatWorkspaceProps = {
  mode: AgentMode;
  messages: ChatMessage[];
  runStatus: RunStatus;
  selectedId?: string;
  onModeChange: (mode: AgentMode) => void;
  onSend: (message: string) => void;
  onSelectMessage?: (id: string) => void;
  onStop: () => void;
};

export function ChatWorkspace({
  mode,
  messages,
  runStatus,
  selectedId,
  onModeChange,
  onSend,
  onSelectMessage,
  onStop,
}: ChatWorkspaceProps) {
  const [message, setMessage] = useState("");
  const isRunning = runStatus === "running";
  const messagesRef = useRef<HTMLDivElement>(null);

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

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || isRunning) {
      return;
    }
    setMessage("");
    onSend(trimmed);
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
      </header>

      <div className="messages" ref={messagesRef}>
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">
              <Activity size={22} aria-hidden="true" />
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
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>
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
        <label className="sr-only" htmlFor="message-input">
          消息
        </label>
        <input
          id="message-input"
          aria-label="消息"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="描述告警事件或提出运维问题..."
          disabled={isRunning}
        />
        {isRunning ? (
          <button className="icon-button" type="button" onClick={onStop} aria-label="停止">
            <Square size={17} aria-hidden="true" />
          </button>
        ) : (
          <button className="icon-button primary" type="submit" aria-label="发送">
            <Send size={17} aria-hidden="true" />
          </button>
        )}
      </form>
    </section>
  );
}
