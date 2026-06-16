import { Send, Square } from "lucide-react";
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
          <h2>Operations Assistant</h2>
          <p>{isRunning ? "Streaming agent execution" : "Ready"}</p>
        </div>
        <label className="mode-select">
          <span>Agent mode</span>
          <select value={mode} onChange={(event) => onModeChange(event.target.value as AgentMode)}>
            <option value="auto">Auto</option>
            <option value="rag">Knowledge</option>
            <option value="oncall">OnCall</option>
          </select>
        </label>
      </header>

      <div className="messages">
        {messages.length === 0 ? (
          <div className="empty-state">Ask a question or start an OnCall diagnosis.</div>
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
          Message
        </label>
        <input
          id="message-input"
          aria-label="Message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Describe an incident or ask a question"
        />
        {isRunning ? (
          <button className="icon-button" type="button" onClick={onStop} aria-label="Stop">
            <Square size={18} aria-hidden="true" />
          </button>
        ) : (
          <button className="icon-button primary" type="submit" aria-label="Send">
            <Send size={18} aria-hidden="true" />
          </button>
        )}
      </form>
    </section>
  );
}
