import { Activity, Send, Square } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import type { AgentMode, ChatMessage, RunStatus } from "../types/events";

type ChatWorkspaceProps = {
  mode: AgentMode;
  messages: ChatMessage[];
  runStatus: RunStatus;
  onModeChange: (mode: AgentMode) => void;
  onSend: (message: string) => void;
  onStop: () => void;
};

export function ChatWorkspace({
  mode,
  messages,
  runStatus,
  onModeChange,
  onSend,
  onStop,
}: ChatWorkspaceProps) {
  const [message, setMessage] = useState("");
  const isRunning = runStatus === "running";
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
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
            <option value="oncall">OnCall</option>
          </select>
        </label>
      </header>

      <div className="messages">
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">
              <Activity size={22} aria-hidden="true" />
            </div>
            <strong className="empty-state-title">运维助手已就绪</strong>
            <span className="empty-state-hint">描述一个告警事件，或向知识库提问</span>
          </div>
        ) : (
          messages.map((item) => (
            <article className={`message ${item.role}`} key={item.id}>
              <div className="message-bubble">{item.content}</div>
            </article>
          ))
        )}
        <div ref={messagesEndRef} />
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
