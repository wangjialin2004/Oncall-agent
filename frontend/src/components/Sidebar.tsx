import { Activity, Gauge, LogOut, MessageSquarePlus, Trash2 } from "lucide-react";

import type { ConversationSummary } from "../api/conversationApi";

type SidebarView = "chat" | "baseline";

type SidebarProps = {
  username: string;
  activeView: SidebarView;
  sessions: ConversationSummary[];
  activeSessionId: string;
  onNewSession: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onOpenBaselines: () => void;
  onLogout: () => void;
};

export function Sidebar({
  username,
  activeView,
  sessions,
  activeSessionId,
  onNewSession,
  onSelectSession,
  onDeleteSession,
  onOpenBaselines,
  onLogout,
}: SidebarProps) {
  const avatarLetter = username.charAt(0).toUpperCase() || "U";

  return (
    <div className="sidebar-inner">
      <div className="sidebar-brand">
        <div className="sidebar-brand-icon">
          <Activity size={15} color="#fff" aria-hidden="true" />
        </div>
        <h1>Agent Gateway</h1>
      </div>

      <button className="sidebar-action" type="button" onClick={onNewSession}>
        <MessageSquarePlus size={15} aria-hidden="true" />
        新建会话
      </button>

      <button
        className={`sidebar-action${activeView === "baseline" ? " active" : ""}`}
        type="button"
        onClick={onOpenBaselines}
      >
        <Gauge size={15} aria-hidden="true" />
        服务基线
      </button>

      <div className="sidebar-section">
        <span className="sidebar-section-label">历史会话</span>
        {sessions.length === 0 ? (
          <p>暂无历史记录</p>
        ) : (
          <ul className="session-list">
            {sessions.map((session) => {
              const isActive = activeView === "chat" && session.session_id === activeSessionId;
              return (
                <li
                  key={session.session_id}
                  className={`session-item${isActive ? " active" : ""}`}
                >
                  <button
                    type="button"
                    className="session-open"
                    title={session.title}
                    onClick={() => onSelectSession(session.session_id)}
                  >
                    <span className="session-title">{session.title || "未命名会话"}</span>
                    <span className="session-meta">{session.turn_count} 轮</span>
                  </button>
                  <button
                    type="button"
                    className="session-delete"
                    aria-label={`删除会话 ${session.title}`}
                    onClick={() => onDeleteSession(session.session_id)}
                  >
                    <Trash2 size={13} aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-user">
          <div className="sidebar-user-avatar" aria-hidden="true">
            {avatarLetter}
          </div>
          <span className="sidebar-username">{username}</span>
        </div>
        <button className="sidebar-logout" type="button" onClick={onLogout}>
          <LogOut size={13} aria-hidden="true" />
          退出登录
        </button>
      </div>
    </div>
  );
}
